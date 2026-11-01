// ==========================================================================
// BIBLER CARTE - couche "Personnels en poste" sur la carte d'accueil
// (spec 2026-11-01-pont-bibler-consignes, section 4.3)
//
// Postes Bibler (etat, effectif, consignes en vigueur) lus par le relais de
// Cockpit (/api/bibler/*, bibler_postes.py), jamais Bibler en direct. Toute
// donnee Bibler est inseree par textContent / API DOM (jamais innerHTML avec
// des donnees).
//
// Consignes : formulaire "Donner une consigne" dans #bibler-panneau-actions
// (si window.BIBLER_PUBLISH), liste "Consignes (n)" de la barre, detail et
// suivi d'une consigne dans le panneau avec surlignage des postes cibles,
// modifier et lever. Ecritures : en-tete X-CSRFToken (meta csrf-token),
// creation avec X-Idempotency-Key (une cle par ouverture du formulaire : un
// double clic ou un nouvel essai apres erreur reseau rejoue la meme creation).
// Un formulaire quitte pendant sa publication (autre poste, panneau ferme) est
// garde avec sa cle : rouvert sur le meme poste, il reprend la meme cle (un
// nouvel essai ne cree pas de doublon). Annuler est grise pendant l'envoi.
// Lien direct : /?bibler=consigne:<id> active la couche et surligne la cible.
//
// Expose : window.CockpitBibler = { activer, desactiver, ouvrirPoste(numero),
// surlignerConsigne(id) }.
// ==========================================================================

(function () {
  "use strict";

  var ZOOM_DETAIL = 15;       // en dessous : regroupement par grille
  var CELLULE_PX = 40;        // taille d'une cellule de regroupement
  var DIRECT_MS = 60000;      // actualisation en direct
  var SUGGESTIONS_MAX = 8;

  var COULEURS = {
    pourvu: "#16a34a",
    incomplet: "#f59e0b",
    en_retard: "#f59e0b",
    vide: "#dc2626",
    a_venir: "#94a3b8"
  };
  var LIBELLES_ETAT = {
    pourvu: "Pourvu",
    incomplet: "Incomplet",
    en_retard: "En retard",
    vide: "Vide",
    a_venir: "À venir"
  };
  // Pire etat d'une grappe (le plus urgent l'emporte)
  var RANG_ETAT = { vide: 0, en_retard: 1, incomplet: 2, pourvu: 3, a_venir: 4 };
  var COULEUR_CONSIGNE = "#7c3aed";

  // --- Etat ---
  var map = null;
  var ctrlBtn = null;
  var couche = null;
  var haut = null;            // bloc .bibler-haut (barre compacte + bandeau)
  var bandeau = null;
  var champHeure = null;
  var btnDirect = null;
  var champRecherche = null;
  var listeSuggestions = null;
  var btnConsignes = null;
  var panneau = null;

  var actif = false;
  var direct = true;
  var instantChoisi = "";     // valeur du champ datetime-local (heure de Paris)
  var donnees = null;         // derniere reponse { mode, instant, postes }
  var cleChargee = "";        // "evenement:annee" des donnees affichees
  var cleVue = "";            // "evenement:annee" de la derniere demande
  var grise = false;          // Bibler indisponible : derniere couche grisee
  var erreurCode = "";
  var timer = null;
  var genListe = 0;
  var genFiche = 0;
  var genConsignes = 0;
  var dernierChargement = 0;
  var posteOuvert = null;
  var suggestionsCourantes = [];
  var suggestionActive = -1;

  // Consignes
  var boiteConsignes = null;  // bouton "Consignes (n)" + liste deroulante
  var listeConsignes = null;
  var consignesListe = null;  // derniere liste du relais (toutes les consignes)
  var consignesErreur = "";
  var consigneOuverte = null; // id de la consigne affichee dans le panneau
  var genDetail = 0;
  var surlignage = null;      // { id, cible, ajuste } : postes cibles surlignes
  var formulaire = null;      // formulaire "Donner une consigne" en cours
  var formulairesGardes = {}; // numero -> formulaire quitte pendant sa publication (meme cle)
  var notePublication = null; // { numero, texte } apres une publication

  // ------------------------------------------------------------------------
  // Utilitaires
  // ------------------------------------------------------------------------

  function el(tag, cls, texte) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (texte !== undefined && texte !== null) n.textContent = String(texte);
    return n;
  }

  function icone(nom) {
    var s = el("span", "material-symbols-outlined", nom);
    s.setAttribute("aria-hidden", "true");
    return s;
  }

  function sansAccents(t) {
    return String(t || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
  }

  function cleCourante() {
    return (window.selectedEvent || "") + ":" + (window.selectedYear || "");
  }

  function parametres(avecInstant) {
    var p = "event=" + encodeURIComponent(window.selectedEvent || "") +
      "&year=" + encodeURIComponent(window.selectedYear || "");
    if (avecInstant && !direct && instantChoisi) p += "&a=" + encodeURIComponent(instantChoisi);
    return p;
  }

  var FMT_HEURE = { hour: "2-digit", minute: "2-digit", timeZone: "Europe/Paris" };
  var FMT_JOUR = { day: "2-digit", month: "2-digit", timeZone: "Europe/Paris" };

  function heure(iso) {
    if (!iso) return "";
    var d = new Date(iso);
    if (isNaN(d)) return String(iso);
    return d.toLocaleTimeString("fr-FR", FMT_HEURE);
  }

  // "HH:MM", precede du jour si ce n'est pas aujourd'hui
  function heureEtJour(iso) {
    var d = new Date(iso);
    if (isNaN(d)) return String(iso || "");
    var jour = d.toLocaleDateString("fr-FR", FMT_JOUR);
    var auj = new Date().toLocaleDateString("fr-FR", FMT_JOUR);
    var h = d.toLocaleTimeString("fr-FR", FMT_HEURE);
    return jour === auj ? h : h + " (" + jour + ")";
  }

  function lireJson(r) {
    return r.json().catch(function () { return null; }).then(function (corps) {
      return { statut: r.status, corps: corps || {} };
    });
  }

  // Message d'erreur d'une reponse du relais (format Cockpit ou Bibler)
  function erreurDe(rep) {
    var c = rep.corps || {};
    var code = c.erreur || c.code || "";
    if (code === "bibler_refus_service") {
      return { code: "refus_service", texte: "Bibler refuse l'accès de Cockpit : configuration du pont à vérifier" };
    }
    if (rep.statut === 502 || rep.statut === 503 || code === "bibler_indisponible" ||
        code === "bibler_non_configure") {
      return { code: "indisponible", texte: "Bibler indisponible" };
    }
    if (code === "evenement_inconnu") {
      return { code: "evenement_inconnu", texte: "Événement inconnu de Bibler" };
    }
    if (code === "droit_insuffisant") return { code: "droit", texte: "Droit insuffisant pour voir les postes Bibler" };
    if (code === "poste_inconnu") return { code: code, texte: "Poste inconnu ou inactif dans Bibler" };
    return { code: code || "erreur", texte: c.message || c.error || ("Erreur Bibler (" + rep.statut + ")") };
  }

  // ------------------------------------------------------------------------
  // Controle (groupe oeil)
  // ------------------------------------------------------------------------

  function ajouterControle() {
    // Ajoute en haut a gauche : l'observateur de map_view.js (setupMapTools)
    // le range alors dans le groupe oeil (RIGHT_DATA), comme pcorg.js.
    var ctrl = L.control({ position: "topleft" });
    ctrl.onAdd = function () {
      var div = L.DomUtil.create("div", "leaflet-bar cockpit-measure-tools cockpit-bibler-ctrl");
      var btn = document.createElement("button");
      btn.className = "tile-btn";
      btn.type = "button";
      btn.id = "map-bibler-btn";
      btn.title = "Personnels en poste";
      var ico = icone("badge");
      ico.style.fontSize = "20px";
      btn.appendChild(ico);
      div.appendChild(btn);
      L.DomEvent.disableClickPropagation(div);
      btn.addEventListener("click", function () { if (actif) desactiver(); else activer(); });
      ctrlBtn = btn;
      return div;
    };
    ctrl.addTo(map);
  }

  // ------------------------------------------------------------------------
  // Barre compacte : [Direct | jour et heure] [recherche] [Consignes (n)]
  // ------------------------------------------------------------------------

  function construireBarre() {
    var conteneur = map.getContainer();
    var barre = el("div", "bibler-barre");
    barre.id = "bibler-barre";

    var temps = el("div", "bibler-temps");
    btnDirect = el("button", "bibler-btn bibler-direct", "Direct");
    btnDirect.type = "button";
    btnDirect.title = "Présence en direct, actualisée chaque minute";
    btnDirect.addEventListener("click", passerEnDirect);
    champHeure = el("input", "bibler-heure");
    champHeure.type = "datetime-local";
    champHeure.title = "Jour et heure (passé : présence réelle ; futur : prévu)";
    champHeure.addEventListener("change", function () {
      if (!champHeure.value) { passerEnDirect(); return; }
      direct = false;
      instantChoisi = champHeure.value;
      arreterTimer();
      majTemps();
      charger();
    });
    temps.appendChild(btnDirect);
    temps.appendChild(champHeure);

    var recherche = el("div", "bibler-recherche");
    champRecherche = el("input", "bibler-champ");
    champRecherche.type = "search";
    champRecherche.placeholder = "N\u00B0 ou nom de poste";
    champRecherche.setAttribute("autocomplete", "off");
    champRecherche.setAttribute("aria-label", "Rechercher un poste");
    champRecherche.addEventListener("input", majSuggestions);
    champRecherche.addEventListener("focus", majSuggestions);
    champRecherche.addEventListener("keydown", toucheRecherche);
    champRecherche.addEventListener("blur", function () {
      setTimeout(fermerSuggestions, 150);
    });
    listeSuggestions = el("ul", "bibler-suggestions");
    listeSuggestions.setAttribute("role", "listbox");
    recherche.appendChild(champRecherche);
    recherche.appendChild(listeSuggestions);

    // "Consignes (n)" : liste deroulante des consignes en vigueur
    boiteConsignes = el("div", "bibler-consignes-boite");
    btnConsignes = el("button", "bibler-btn bibler-consignes-btn", "Consignes");
    btnConsignes.type = "button";
    btnConsignes.id = "bibler-consignes-btn";
    btnConsignes.setAttribute("aria-haspopup", "listbox");
    btnConsignes.setAttribute("aria-expanded", "false");
    btnConsignes.title = "Consignes en vigueur : suivi et postes ciblés";
    btnConsignes.addEventListener("click", basculerListeConsignes);
    listeConsignes = el("ul", "bibler-consignes-liste");
    listeConsignes.id = "bibler-consignes-liste";
    listeConsignes.setAttribute("role", "listbox");
    listeConsignes.addEventListener("keydown", function (e) {
      if (e.key === "Escape") { fermerListeConsignes(); btnConsignes.focus(); }
    });
    boiteConsignes.appendChild(btnConsignes);
    boiteConsignes.appendChild(listeConsignes);
    document.addEventListener("mousedown", function (e) {
      if (listeConsignes.classList.contains("open") && !boiteConsignes.contains(e.target)) fermerListeConsignes();
    });

    barre.appendChild(temps);
    barre.appendChild(recherche);
    barre.appendChild(boiteConsignes);

    bandeau = el("div", "bibler-bandeau");
    bandeau.id = "bibler-bandeau";
    bandeau.setAttribute("role", "status");
    bandeau.hidden = true;

    haut = el("div", "bibler-haut");
    haut.id = "bibler-haut";
    haut.appendChild(barre);
    haut.appendChild(bandeau);
    L.DomEvent.disableClickPropagation(haut);
    L.DomEvent.disableScrollPropagation(haut);
    conteneur.appendChild(haut);
    majTemps();
  }

  function passerEnDirect() {
    direct = true;
    instantChoisi = "";
    if (champHeure) champHeure.value = "";
    majTemps();
    charger();
    demarrerTimer();
  }

  function majTemps() {
    if (btnDirect) btnDirect.classList.toggle("active", direct);
    if (champHeure) champHeure.classList.toggle("active", !direct);
  }

  function majCompteurConsignes(n) {
    if (!btnConsignes) return;
    btnConsignes.textContent = typeof n === "number" ? "Consignes (" + n + ")" : "Consignes";
  }

  function chargerCompteurConsignes() {
    var cle = cleCourante();
    var gen = ++genConsignes;
    fetch("/api/bibler/consignes?" + parametres(false), { credentials: "same-origin" })
      .then(lireJson)
      .then(function (rep) {
        if (gen !== genConsignes || !actif || cle !== cleCourante()) return;
        var liste = rep.statut === 200 && rep.corps && rep.corps.consignes;
        if (!Array.isArray(liste)) {
          consignesErreur = erreurDe(rep).texte;
          majCompteurConsignes(null);
        } else {
          consignesListe = liste.filter(function (c) { return c && c.id; });
          consignesErreur = "";
          majCompteurConsignes(consignesEnVigueur().length);
        }
        rendreListeConsignes();
      })
      .catch(function () {
        if (gen !== genConsignes) return;
        consignesErreur = "Bibler indisponible";
        majCompteurConsignes(null);
        rendreListeConsignes();
      });
  }

  function consignesEnVigueur() {
    return (consignesListe || []).filter(function (c) { return c.etat === "en_vigueur"; });
  }

  // Bandeau : erreur, ou mode de la vue figee ("Presence reelle a" / "Prevu a")
  function majBandeau() {
    if (!bandeau) return;
    bandeau.className = "bibler-bandeau";
    var texte = "";
    if (erreurCode === "indisponible") {
      texte = donnees ? "Bibler indisponible : dernière situation connue, grisée" : "Bibler indisponible";
      bandeau.classList.add("bibler-bandeau-erreur");
    } else if (erreurCode) {
      texte = erreurCode;
      bandeau.classList.add("bibler-bandeau-erreur");
    } else if (donnees && donnees.mode === "prevu") {
      texte = "Prévu à " + heureEtJour(donnees.instant) +
        " : planning seulement, aucune présence constatée (vert = prévu)";
      bandeau.classList.add("bibler-bandeau-prevu");
    } else if (donnees && !donnees.direct) {
      texte = "Présence réelle à " + heureEtJour(donnees.instant);
      bandeau.classList.add("bibler-bandeau-reel");
    }
    var sans = donnees ? donnees.postes.filter(function (p) { return !p.coords; }).length : 0;
    if (texte && sans && !erreurCode) texte += " - " + sans + " poste(s) sans position (recherche)";
    bandeau.textContent = texte;
    bandeau.hidden = !texte;
    if (btnDirect && donnees && donnees.direct && !erreurCode) {
      btnDirect.title = "Présence en direct, actualisée chaque minute (maj " + heure(donnees.instant) + ")";
    }
  }

  // ------------------------------------------------------------------------
  // Chargement de la liste des postes
  // ------------------------------------------------------------------------

  function charger() {
    if (!actif || !map) return;
    if (!window.selectedEvent || !window.selectedYear) return;
    var gen = ++genListe;
    var cle = cleCourante();
    if (cle !== cleVue) chargerCompteurConsignes();
    cleVue = cle;
    dernierChargement = Date.now();
    var params = parametres(true);
    var enDirect = direct;
    fetch("/api/bibler/postes?" + params, { credentials: "same-origin" })
      .then(lireJson)
      .then(function (rep) {
        if (gen !== genListe || !actif) return;
        if (rep.statut === 200 && rep.corps && Array.isArray(rep.corps.postes)) {
          donnees = { mode: rep.corps.mode, instant: rep.corps.instant, postes: rep.corps.postes,
                      params: params, direct: enDirect };
          cleChargee = cle;
          grise = false;
          erreurCode = "";
        } else {
          var err = erreurDe(rep);
          traiterErreurListe(err, cle);
        }
        rendre();
        ajusterSurlignage();
        majBandeau();
        synchroniserPanneau();
      })
      .catch(function () {
        if (gen !== genListe || !actif) return;
        traiterErreurListe({ code: "indisponible", texte: "Bibler indisponible" }, cle);
        rendre();
        ajusterSurlignage();
        majBandeau();
        synchroniserPanneau();
      });
  }

  // Le panneau suit toujours la liste affichee (meme instant, meme mode que
  // le bandeau) : apres chaque chargement de la liste, la fiche est relue
  // avec les parametres de cette liste, ou reaffichee telle quelle (grisee)
  // si Bibler ne repond plus.
  function synchroniserPanneau() {
    if (consigneOuverte && panneau && panneau._majCibles) panneau._majCibles();
    if (posteOuvert === null || !panneau || panneau.hidden) return;
    var fiche = panneau._fiche;
    var memeFiche = fiche && fiche.numero === posteOuvert;
    if (donnees && !grise) {
      ouvrirPoste(posteOuvert, true);
    } else if (memeFiche && donnees && panneau._params === donnees.params) {
      remplirPanneau(fiche, panneau._params);
    } else if (!donnees) {
      afficherErreurPanneau(posteOuvert, erreurCode || "Bibler indisponible");
    }
  }

  function traiterErreurListe(err, cle) {
    if (err.code === "indisponible") {
      // Derniere couche gardee, grisee, si elle est du meme evenement
      if (cle !== cleChargee) { donnees = null; cleChargee = ""; }
      grise = true;
      erreurCode = "indisponible";
      return;
    }
    // Evenement inconnu, droit, autre : pas de couche trompeuse
    donnees = null;
    cleChargee = "";
    grise = false;
    erreurCode = err.texte;
  }

  // ------------------------------------------------------------------------
  // Rendu des marqueurs (grille simple au dezoom)
  // ------------------------------------------------------------------------

  function styleMarqueur(p, selection) {
    var prevu = donnees && donnees.mode === "prevu";
    var consigne = (p.consignes || 0) > 0;
    var s = {
      radius: selection ? 10 : 7,
      fillColor: COULEURS[p.etat] || COULEURS.a_venir,
      fillOpacity: grise ? 0.4 : 0.9,
      color: consigne ? COULEUR_CONSIGNE : "#ffffff",
      weight: consigne ? 4 : 1.5,
      opacity: grise ? 0.4 : 1,
      dashArray: prevu ? "3 3" : null
    };
    if (surlignage && surlignage.cible) {
      if (estCible(p, surlignage.cible)) {
        s.color = couleurSurlignage();
        s.weight = 4;
        s.radius = 11;
        s.fillOpacity = grise ? 0.4 : 1;
      } else {
        s.fillOpacity = 0.3;
        s.opacity = 0.35;
      }
    }
    return s;
  }

  function infobulle(p) {
    var d = el("div", "bibler-infobulle");
    d.appendChild(el("strong", null, "Poste " + p.numero));
    if (p.affectation) d.appendChild(el("div", null, p.affectation));
    var prevu = donnees && donnees.mode === "prevu";
    d.appendChild(el("div", null, (LIBELLES_ETAT[p.etat] || p.etat || "") + " - " +
      (p.presents || 0) + "/" + (p.planifies || 0) + (prevu ? " prévu(s)" : " présent(s)")));
    if (p.consignes) d.appendChild(el("div", "bibler-infobulle-consigne", p.consignes + " consigne(s) en vigueur"));
    return d;
  }

  function rendre() {
    if (!couche) return;
    couche.clearLayers();
    if (!donnees) return;
    var postes = donnees.postes.filter(function (p) { return p.coords; });
    var zoom = map.getZoom();
    if (zoom >= ZOOM_DETAIL) {
      postes.forEach(function (p) { couche.addLayer(marqueurPoste(p)); });
      return;
    }
    var cellules = {};
    postes.forEach(function (p) {
      var pt = map.project([p.coords.lat, p.coords.lon], zoom);
      var k = Math.floor(pt.x / CELLULE_PX) + ":" + Math.floor(pt.y / CELLULE_PX);
      (cellules[k] = cellules[k] || []).push(p);
    });
    Object.keys(cellules).forEach(function (k) {
      var groupe = cellules[k];
      if (groupe.length === 1) { couche.addLayer(marqueurPoste(groupe[0])); return; }
      couche.addLayer(marqueurGrappe(groupe, zoom));
    });
  }

  function marqueurPoste(p) {
    var m = L.circleMarker([p.coords.lat, p.coords.lon], styleMarqueur(p, p.numero === posteOuvert));
    m.bindTooltip(infobulle(p), { direction: "top", offset: [0, -6] });
    m.on("click", function () { choisirPoste(p); });
    if (surlignage && surlignage.cible && estCible(p, surlignage.cible)) {
      // Halo a la couleur de la priorite : la cible se lit d'un coup d'oeil
      var c = couleurSurlignage();
      var halo = L.circleMarker([p.coords.lat, p.coords.lon], {
        radius: 20, color: c, weight: 2, opacity: 0.9, fillColor: c, fillOpacity: 0.18, interactive: false
      });
      return L.layerGroup([halo, m]);
    }
    return m;
  }

  function marqueurGrappe(groupe, zoom) {
    var lat = 0, lon = 0, pire = "a_venir", consignes = 0;
    groupe.forEach(function (p) {
      lat += p.coords.lat;
      lon += p.coords.lon;
      if ((RANG_ETAT[p.etat] !== undefined ? RANG_ETAT[p.etat] : 9) < RANG_ETAT[pire]) pire = p.etat;
      consignes += p.consignes || 0;
    });
    var centre = L.latLng(lat / groupe.length, lon / groupe.length);
    var cls = "bibler-grappe bibler-etat-" + (COULEURS[pire] ? pire : "a_venir");
    if (consignes) cls += " bibler-grappe-consigne";
    if (surlignage && surlignage.cible) {
      cls += groupe.some(function (p) { return estCible(p, surlignage.cible); }) ?
        " bibler-grappe-cible bibler-grappe-cible-" + (surlignage.prio || "information") : " bibler-grappe-estompe";
    }
    if (grise) cls += " bibler-grise";
    if (donnees && donnees.mode === "prevu") cls += " bibler-prevu";
    var contenu = el("span", null, groupe.length);
    var m = L.marker(centre, {
      icon: L.divIcon({ className: cls, html: contenu, iconSize: [30, 30], iconAnchor: [15, 15] }),
      keyboard: false
    });
    m.bindTooltip(groupe.length + " postes", { direction: "top", offset: [0, -14] });
    m.on("click", function () { map.flyTo(centre, Math.min(zoom + 2, ZOOM_DETAIL)); });
    return m;
  }

  // ------------------------------------------------------------------------
  // Recherche et suggestions (filtre local sur la liste chargee)
  // ------------------------------------------------------------------------

  function filtrer(q) {
    if (!donnees) return [];
    var t = sansAccents(q).trim();
    if (!t) return [];
    var res;
    if (/^\d+$/.test(t)) {
      res = donnees.postes.filter(function (p) { return String(p.numero).indexOf(t) === 0; });
      res.sort(function (a, b) {
        return (String(a.numero) === t ? 0 : 1) - (String(b.numero) === t ? 0 : 1) || a.numero - b.numero;
      });
    } else {
      res = donnees.postes.filter(function (p) {
        return [p.affectation, p.metier, p.departement, p.zone, p.secteur].some(function (c) {
          return sansAccents(c).indexOf(t) !== -1;
        });
      });
    }
    return res.slice(0, SUGGESTIONS_MAX);
  }

  function majSuggestions() {
    if (!listeSuggestions) return;
    suggestionsCourantes = filtrer(champRecherche.value);
    suggestionActive = suggestionsCourantes.length ? 0 : -1;
    while (listeSuggestions.firstChild) listeSuggestions.removeChild(listeSuggestions.firstChild);
    if (!champRecherche.value.trim()) { fermerSuggestions(); return; }
    if (!suggestionsCourantes.length) {
      listeSuggestions.appendChild(el("li", "bibler-suggestion-vide",
        donnees ? "Aucun poste" : "Postes non chargés"));
    }
    suggestionsCourantes.forEach(function (p, i) {
      var li = el("li", "bibler-suggestion");
      li.setAttribute("role", "option");
      var pastille = el("span", "bibler-pastille");
      pastille.style.background = COULEURS[p.etat] || COULEURS.a_venir;
      li.appendChild(pastille);
      var txt = el("span", "bibler-suggestion-texte");
      txt.appendChild(el("strong", null, p.numero));
      txt.appendChild(document.createTextNode(" " + (p.affectation || "")));
      var lieu = [p.zone, p.secteur].filter(Boolean).join(" / ");
      if (lieu) txt.appendChild(el("small", null, lieu));
      li.appendChild(txt);
      if (!p.coords) li.appendChild(el("span", "bibler-sans-position", "sans position"));
      li.addEventListener("mousedown", function (e) { e.preventDefault(); });
      li.addEventListener("click", function () { choisirPoste(p); fermerSuggestions(); });
      if (i === suggestionActive) li.classList.add("active");
      listeSuggestions.appendChild(li);
    });
    listeSuggestions.classList.add("open");
  }

  function fermerSuggestions() {
    if (listeSuggestions) listeSuggestions.classList.remove("open");
  }

  function toucheRecherche(e) {
    var items = listeSuggestions ? listeSuggestions.querySelectorAll(".bibler-suggestion") : [];
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      if (!items.length) return;
      e.preventDefault();
      suggestionActive = (suggestionActive + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
      Array.prototype.forEach.call(items, function (li, i) { li.classList.toggle("active", i === suggestionActive); });
    } else if (e.key === "Enter") {
      e.preventDefault();
      var p = suggestionsCourantes[suggestionActive];
      if (p) { choisirPoste(p); fermerSuggestions(); champRecherche.blur(); }
    } else if (e.key === "Escape") {
      fermerSuggestions();
    }
  }

  function choisirPoste(p) {
    if (p.coords) map.flyTo([p.coords.lat, p.coords.lon], Math.max(map.getZoom(), 17));
    ouvrirPoste(p.numero);
  }

  // ------------------------------------------------------------------------
  // Panneau du poste (#bibler-panneau)
  // ------------------------------------------------------------------------

  function construirePanneau() {
    panneau = el("aside", "bibler-panneau");
    panneau.id = "bibler-panneau";
    panneau.hidden = true;
    L.DomEvent.disableClickPropagation(panneau);
    L.DomEvent.disableScrollPropagation(panneau);
    map.getContainer().appendChild(panneau);
  }

  function viderPanneau() {
    while (panneau.firstChild) panneau.removeChild(panneau.firstChild);
  }

  function fermerPanneau() {
    posteOuvert = null;
    genFiche++;
    consigneOuverte = null;
    genDetail++;
    surlignage = null;
    notePublication = null;
    jeterFormulaire();
    if (panneau) {
      viderPanneau();
      panneau.hidden = true;
      panneau._majCibles = null;
    }
    rendre();
  }

  function enteteSimple(titre) {
    var h = el("div", "bibler-panneau-entete");
    h.appendChild(el("div", "bibler-panneau-titre", titre));
    var fermer = el("button", "bibler-panneau-fermer");
    fermer.type = "button";
    fermer.title = "Fermer";
    fermer.setAttribute("aria-label", "Fermer");
    fermer.appendChild(icone("close"));
    fermer.addEventListener("click", fermerPanneau);
    h.appendChild(fermer);
    return h;
  }

  function section(titre) {
    var s = el("section", "bibler-section");
    if (titre) s.appendChild(el("h4", "bibler-section-titre", titre));
    return s;
  }

  function ouvrirPoste(numero, silencieux) {
    // Parametres de la liste affichee : la fiche a le meme instant et le meme
    // mode que le bandeau (sinon les agents d'une heure prevue seraient lus
    // comme presents).
    numero = parseInt(numero, 10);
    if (isNaN(numero)) return;
    if (!actif) activer();
    if (!panneau) return;
    if (!silencieux) {
      // Choix d'un poste : fin du detail de consigne et de son surlignage ;
      // un formulaire en cours ne survit que pour son propre poste.
      consigneOuverte = null;
      genDetail++;
      surlignage = null;
      panneau._majCibles = null;
      if (formulaire && formulaire.numero !== numero) jeterFormulaire();
      if (notePublication && notePublication.numero !== numero) notePublication = null;
    }
    posteOuvert = numero;
    rendre();
    var gen = ++genFiche;
    if (!silencieux) {
      viderPanneau();
      panneau.appendChild(enteteSimple("Poste " + numero));
      panneau.appendChild(el("div", "bibler-panneau-info", "Chargement..."));
      panneau.hidden = false;
    }
    var params = donnees ? donnees.params : parametres(true);
    function aJour() {
      return gen === genFiche && posteOuvert === numero && (!donnees || donnees.params === params);
    }
    function echec(err) {
      var fiche = panneau._fiche;
      if (err.code === "indisponible" && fiche && fiche.numero === numero && panneau._params === params) {
        remplirPanneau(fiche, params);   // derniere fiche gardee, etiquetee "Bibler indisponible"
      } else {
        afficherErreurPanneau(numero, err.texte);
      }
    }
    fetch("/api/bibler/postes/" + numero + "?" + params, { credentials: "same-origin" })
      .then(lireJson)
      .then(function (rep) {
        if (!aJour()) return;
        if (rep.statut === 200 && rep.corps && rep.corps.numero !== undefined) {
          remplirPanneau(rep.corps, params);
        } else {
          echec(erreurDe(rep));
        }
      })
      .catch(function () {
        if (!aJour()) return;
        echec({ code: "indisponible", texte: "Bibler indisponible" });
      });
  }

  function afficherErreurPanneau(numero, texte) {
    panneau._fiche = null;
    panneau._params = null;
    viderPanneau();
    panneau.appendChild(enteteSimple("Poste " + numero));
    panneau.appendChild(el("div", "bibler-panneau-info bibler-panneau-erreur", texte));
    panneau.hidden = false;
  }

  function remplirPanneau(f, params) {
    // Reaffichage du meme poste : sections ouvertes, defilement et saisie en
    // cours dans le formulaire de consigne gardes
    var focus = memoriserFocus();
    var ouverts = {}, defilement = 0;
    if (panneau._fiche && panneau._fiche.numero === f.numero) {
      Array.prototype.forEach.call(panneau.querySelectorAll("details[open] > summary"), function (s) {
        ouverts[s.textContent] = true;
      });
      var ancien = panneau.querySelector(".bibler-panneau-corps");
      if (ancien) defilement = ancien.scrollTop;
    }
    viderPanneau();
    var resume = donnees && donnees.postes.filter(function (p) { return p.numero === f.numero; })[0];
    var prevu = donnees && donnees.mode === "prevu";

    panneau.appendChild(enteteSimple("Poste " + f.numero));
    var tete = el("div", "bibler-panneau-tete");
    if (f.affectation) tete.appendChild(el("div", "bibler-affectation", f.affectation));
    if (resume) {
      var ligne = el("div", "bibler-etat-ligne");
      var puce = el("span", "bibler-etat-puce", LIBELLES_ETAT[resume.etat] || resume.etat);
      puce.style.background = COULEURS[resume.etat] || COULEURS.a_venir;
      ligne.appendChild(puce);
      ligne.appendChild(el("span", "bibler-effectif",
        (resume.presents || 0) + "/" + (resume.planifies || 0) + (prevu ? " prévu(s)" : " présent(s)")));
      if (prevu) ligne.appendChild(el("span", "bibler-tag-prevu", "Prévu à " + heureEtJour(donnees.instant)));
      else if (donnees && !donnees.direct) ligne.appendChild(el("span", "bibler-tag-reel", "à " + heureEtJour(donnees.instant)));
      if (grise) ligne.appendChild(el("span", "bibler-tag-grise", "Bibler indisponible"));
      tete.appendChild(ligne);
    }
    if (!f.coords) tete.appendChild(el("div", "bibler-sans-position", "sans position sur la carte"));
    panneau.appendChild(tete);

    var corps = el("div", "bibler-panneau-corps");

    // Structure et chef
    var st = f.structure || {};
    var chemin = [st.metier, st.departement, st.zone, st.secteur].filter(Boolean).join(" > ");
    if (chemin || f.chef) {
      var s0 = section(null);
      if (chemin) s0.appendChild(el("div", "bibler-chemin", chemin));
      if (f.chef) {
        var ch = el("div", "bibler-chef");
        ch.appendChild(el("span", "bibler-libelle", "Chef : "));
        ch.appendChild(document.createTextNode(f.chef));
        s0.appendChild(ch);
      }
      corps.appendChild(s0);
    }

    // Mission et positionnement repliables
    var mi = f.mission || {};
    var aMission = mi.texte || (mi.etapes && mi.etapes.length) || mi.particuliere || (mi.autres && mi.autres.length);
    if (aMission) {
      var dm = el("details", "bibler-details");
      dm.appendChild(el("summary", null, "Mission"));
      if (mi.texte) dm.appendChild(el("p", null, mi.texte));
      if (mi.etapes && mi.etapes.length) {
        var ol = el("ol");
        mi.etapes.forEach(function (e) { ol.appendChild(el("li", null, e)); });
        dm.appendChild(ol);
      }
      if (mi.particuliere) {
        var pp = el("p");
        pp.appendChild(el("span", "bibler-libelle", "Conduite particulière : "));
        pp.appendChild(document.createTextNode(mi.particuliere));
        dm.appendChild(pp);
      }
      (mi.autres || []).forEach(function (a) {
        var pa = el("p");
        pa.appendChild(el("span", "bibler-libelle", (a.libelle || "") + " : "));
        pa.appendChild(document.createTextNode(a.texte || ""));
        dm.appendChild(pa);
      });
      corps.appendChild(dm);
    }
    if (f.positionnement) {
      var dp = el("details", "bibler-details");
      dp.appendChild(el("summary", null, "Positionnement"));
      dp.appendChild(el("p", null, f.positionnement));
      corps.appendChild(dp);
    }
    if (f.materiel && f.materiel.length) {
      var dmat = el("details", "bibler-details");
      dmat.appendChild(el("summary", null, "Matériel (" + f.materiel.length + ")"));
      var ulm = el("ul");
      f.materiel.forEach(function (m) { ulm.appendChild(el("li", null, m)); });
      dmat.appendChild(ulm);
      corps.appendChild(dmat);
    }

    // Specimens controles
    if (f.specimens && f.specimens.length) {
      var ss = section("Spécimens contrôlés (" + f.specimens.length + ")");
      var grille = el("div", "bibler-specimens");
      f.specimens.forEach(function (sp) {
        var c = el("figure", "bibler-specimen");
        if (sp.famille) c.title = sp.famille;
        if (sp.visuel) {
          var img = el("img");
          img.loading = "lazy";
          img.alt = sp.libelle || "";
          img.src = "/api/bibler/visuel/" + encodeURIComponent(sp.visuel);
          img.addEventListener("error", function () { img.remove(); c.classList.add("sans-visuel"); });
          c.appendChild(img);
        } else {
          c.classList.add("sans-visuel");
        }
        var cap = el("figcaption");
        cap.appendChild(el("span", "bibler-specimen-libelle", sp.libelle || ""));
        if (sp.controle) cap.appendChild(el("span", "bibler-controle bibler-controle-" +
          sansAccents(sp.controle).replace(/[^a-z]/g, ""), sp.controle));
        c.appendChild(cap);
        grille.appendChild(c);
      });
      ss.appendChild(grille);
      corps.appendChild(ss);
    }

    // Agents en service
    var sa = section((prevu ? "Agents prévus" : "Agents en service") + " (" + (f.agents || []).length + ")");
    if (!f.agents || !f.agents.length) {
      sa.appendChild(el("div", "bibler-vide", "Aucun agent"));
    } else {
      var ula = el("ul", "bibler-agents");
      f.agents.forEach(function (a) {
        var li = el("li");
        li.appendChild(el("span", "bibler-agent-nom", a.nom || "?"));
        var det = [String(a.etat || "").replace(/_/g, " ")];
        if (a.depuis) det.push("depuis " + heure(a.depuis));
        if (a.format || a.prevu) det.push(a.format || a.prevu);
        li.appendChild(el("span", "bibler-agent-detail", det.filter(Boolean).join(" - ")));
        ula.appendChild(li);
      });
      sa.appendChild(ula);
    }
    corps.appendChild(sa);

    // Shifts du jour
    if (f.vacations && f.vacations.length) {
      var sv = section("Shifts du jour");
      var ulv = el("ul", "bibler-shifts");
      f.vacations.forEach(function (v) {
        var li = el("li");
        li.appendChild(el("span", "bibler-shift-format", v.format || (heure(v.debut) + "-" + heure(v.fin))));
        li.appendChild(el("span", "bibler-shift-agents", (v.agents || 0) + " agent(s)"));
        ulv.appendChild(li);
      });
      sv.appendChild(ulv);
      corps.appendChild(sv);
    }

    // Consignes en vigueur qui visent le poste (Task 9 : surlignage, modifier, lever)
    var sc = section("Consignes en vigueur (" + (f.consignes || []).length + ")");
    var listeC = el("ul", "bibler-consignes");
    listeC.id = "bibler-panneau-consignes";
    if (!f.consignes || !f.consignes.length) {
      sc.appendChild(el("div", "bibler-vide", "Aucune consigne"));
    }
    (f.consignes || []).forEach(function (c) {
      var k = prioConsigne(c);
      var li = el("li", "bibler-consigne bibler-prio-bord-" + k);
      li.dataset.consigne = c.id || "";
      if (c.id) {
        li.classList.add("bibler-cliquable");
        li.tabIndex = 0;
        li.title = "Voir le suivi et les postes ciblés";
        li.addEventListener("click", function () { surlignerConsigne(c.id); });
        li.addEventListener("keydown", function (e) {
          if (e.key === "Enter") { e.preventDefault(); surlignerConsigne(c.id); }
        });
      }
      var t = el("div", "bibler-consigne-titre");
      t.appendChild(iconePrio(k));
      t.appendChild(el("span", "bibler-consigne-titre-texte", c.titre || ""));
      t.appendChild(pucePrio(c, k));
      li.appendChild(t);
      if (c.texte) li.appendChild(el("div", "bibler-consigne-texte", c.texte));
      var meta = [c.cible_libelle, c.auteur, c.heure].filter(Boolean).join(" - ");
      if (meta) li.appendChild(el("div", "bibler-consigne-meta", meta));
      listeC.appendChild(li);
    });
    sc.appendChild(listeC);
    corps.appendChild(sc);

    // Bouton ou formulaire "Donner une consigne" (si window.BIBLER_PUBLISH)
    var actions = el("div", "bibler-panneau-actions");
    actions.id = "bibler-panneau-actions";
    actions.dataset.numero = String(f.numero);
    corps.appendChild(actions);
    monterActions(actions, f);

    panneau.appendChild(corps);
    panneau._fiche = f;
    panneau._params = params || null;
    panneau.hidden = false;
    Array.prototype.forEach.call(panneau.querySelectorAll("details > summary"), function (s) {
      if (ouverts[s.textContent]) s.parentNode.open = true;
    });
    if (defilement) corps.scrollTop = defilement;
    restaurerFocus(focus);
  }

  // ------------------------------------------------------------------------
  // Consignes : outils communs
  // ------------------------------------------------------------------------

  var PRIORITES = [
    { valeur: "information", libelle: "Information" },
    { valeur: "importante", libelle: "Important" },
    { valeur: "urgente", libelle: "Urgent" }
  ];
  var LIBELLES_ETAT_CONSIGNE = {
    en_vigueur: "En vigueur", programmee: "Programmée", levee: "Levée",
    expiree: "Expirée", brouillon: "Brouillon"
  };
  var TITRE_MAX = 120;        // limites de Bibler (TITRE_MAX, TEXTE_MAX)
  var TEXTE_MAX = 600;
  var APERCU_MS = 300;        // anti-rebond de l'apercu des destinataires
  // Priorite d'une consigne : icone et couleur du bandeau, de la bordure et
  // du surlignage des postes cibles
  var PRIORITES_CONSIGNE = {
    information: { icone: "info", couleur: "#2563eb", libelle: "Information" },
    importante: { icone: "warning", couleur: "#d97706", libelle: "Important" },
    urgente: { icone: "priority_high", couleur: "#dc2626", libelle: "Urgent" }
  };
  var RE_ID = /^[0-9a-fA-F]{24}$/;
  var RE_FORMAT = /^\d{2}:\d{2}-\d{2}:\d{2}$/;

  function peutPublier() { return window.BIBLER_PUBLISH === true; }

  function prioConsigne(c) {
    var k = sansAccents((c && c.priorite) || "").replace(/[^a-z]/g, "");
    return PRIORITES_CONSIGNE[k] ? k : "information";
  }

  function couleurSurlignage() {
    return PRIORITES_CONSIGNE[(surlignage && surlignage.prio) || "information"].couleur;
  }

  function iconePrio(k) {
    var i = icone(PRIORITES_CONSIGNE[k].icone);
    i.classList.add("bibler-prio-ico", "bibler-prio-ico-" + k);
    return i;
  }

  function pucePrio(c, k) {
    return el("span", "bibler-prio-puce bibler-prio-puce-" + k, c.priorite_libelle || PRIORITES_CONSIGNE[k].libelle);
  }

  // Ligne "icone + texte" des informations d'une consigne
  function ligneInfo(nom, texte) {
    var d = el("div", "bibler-info");
    d.appendChild(icone(nom));
    var t = el("span", null, texte);
    d.appendChild(t);
    return d;
  }

  // Barre de progression du suivi (lu, transmis)
  function jauge(libelle, n, total, part) {
    var d = el("div", "bibler-jauge" + (total && n >= total ? " bibler-jauge-pleine" : ""));
    var tete = el("div", "bibler-jauge-tete");
    tete.appendChild(el("span", null, libelle));
    tete.appendChild(el("b", null, (n || 0) + "/" + (total || 0) + " (" + (part || 0) + " %)"));
    d.appendChild(tete);
    var fond = el("div", "bibler-jauge-fond");
    var plein = el("div", "bibler-jauge-plein");
    plein.style.width = Math.max(0, Math.min(100, Number(part) || 0)) + "%";
    fond.appendChild(plein);
    d.appendChild(fond);
    return d;
  }

  function jetonCsrf() {
    var m = document.querySelector('meta[name="csrf-token"]');
    return m ? m.getAttribute("content") || "" : "";
  }

  // Ecriture vers le relais ; jeton CSRF perime (code "csrf") : renouvele
  // puis un seul nouvel essai (la requete n'a pas atteint Bibler).
  function ecrire(methode, url, corps, entetes, rejoue) {
    var h = { "Content-Type": "application/json", "X-CSRFToken": jetonCsrf() };
    Object.keys(entetes || {}).forEach(function (k) { h[k] = entetes[k]; });
    return fetch(url, { method: methode, credentials: "same-origin", headers: h, body: JSON.stringify(corps || {}) })
      .then(lireJson)
      .then(function (rep) {
        if (!rejoue && rep.statut === 400 && rep.corps && rep.corps.code === "csrf" && window.CockpitCsrf) {
          return window.CockpitCsrf.refresh().then(function (st) {
            return st && st.ok ? ecrire(methode, url, corps, entetes, true) : rep;
          }, function () { return rep; });
        }
        return rep;
      })
      .catch(function () { return { statut: 0, corps: {} }; });
  }

  // Cle d'idempotence (crypto.randomUUID n'existe qu'en contexte securise)
  function nouvelleCle() {
    var c = window.crypto;
    if (c && typeof c.randomUUID === "function") return c.randomUUID();
    var o = new Uint8Array(16);
    if (c && c.getRandomValues) c.getRandomValues(o);
    else for (var i = 0; i < 16; i++) o[i] = Math.floor(Math.random() * 256);
    o[6] = (o[6] & 0x0f) | 0x40;
    o[8] = (o[8] & 0x3f) | 0x80;
    var h = Array.prototype.map.call(o, function (b) { return (b + 0x100).toString(16).slice(1); }).join("");
    return h.slice(0, 8) + "-" + h.slice(8, 12) + "-" + h.slice(12, 16) + "-" + h.slice(16, 20) + "-" + h.slice(20);
  }

  // Message d'une ecriture refusee ; recharger = la vue doit etre relue
  function messageEcriture(rep, creation) {
    var c = rep.corps || {};
    var code = c.erreur || c.code || "";
    if (code === "bibler_non_configure") return { texte: "Bibler n'est pas configuré dans Cockpit." };
    if (code === "bibler_refus_service") {
      return { texte: "Bibler refuse l'accès de Cockpit : configuration du pont à vérifier (administrateur)." };
    }
    if (rep.statut === 0 || rep.statut === 502 || rep.statut === 503 || code === "bibler_indisponible") {
      return { texte: creation ?
        "Bibler n'a pas répondu : réessayez, la meme publication ne sera pas doublee." :
        "Bibler n'a pas répondu : réessayez." };
    }
    if (code === "requete_en_cours") {
      return { texte: "Publication en cours de traitement par Bibler : reessayez dans quelques secondes " +
        "(sans risque de doublon)." };
    }
    if (code === "idempotence_conflit") {
      return { texte: "Ce formulaire a déjà été envoyé avec un autre contenu : vérifiez la liste des " +
        "consignes, puis fermez et rouvrez le formulaire pour une nouvelle consigne.", recharger: true };
    }
    if (code === "consigne_levee") return { texte: c.message || "Consigne déjà levée.", recharger: true };
    if (rep.statut === 409 || code === "version_perimee") {
      return { texte: "Consigne modifiée entre-temps : elle a été rechargée.", recharger: true };
    }
    if (code === "droit_insuffisant") return { texte: "Droit insuffisant pour donner des consignes." };
    if (code === "csrf") return { texte: "Session expirée : rechargez la page." };
    return { texte: c.message || c.error || ("Erreur Bibler (" + rep.statut + ")") };
  }

  // Le poste p (ligne de /api/bibler/postes) est-il vise par la cible ?
  function estCible(p, cible) {
    if (!cible) return false;
    if (cible.tout_evenement) return true;
    function dans(liste, v) {
      v = String(v === undefined || v === null ? "" : v).trim();
      return !!v && (liste || []).some(function (x) { return String(x).trim() === v; });
    }
    return dans(cible.postes, p.numero) || dans(cible.departements, p.departement) ||
      dans(cible.zones, p.zone) || dans(cible.secteurs, p.secteur);
  }

  function cibleComplete(partielle) {
    partielle = partielle || {};
    return {
      tout_evenement: !!partielle.tout_evenement,
      departements: partielle.departements || [],
      zones: partielle.zones || [],
      secteurs: partielle.secteurs || [],
      postes: partielle.postes || []
    };
  }

  // Parametres de cible des routes formats et apercu
  function parametresCible(c) {
    var p = parametres(false);
    if (c.tout_evenement) return p + "&tout=1";
    ["postes", "secteurs", "zones", "departements"].forEach(function (k) {
      if (c[k] && c[k].length) p += "&" + k + "=" + encodeURIComponent(c[k].join(","));
    });
    return p;
  }

  // Les heures de Bibler finissent par "Z" : envoyees avec un decalage explicite
  function isoAvecDecalage(iso) {
    var t = String(iso || "").trim();
    return /Z$/i.test(t) ? t.slice(0, -1) + "+00:00" : t;
  }

  // Champ de saisie : focus et selection gardes quand le panneau est redessine
  function memoriserFocus() {
    var a = document.activeElement;
    if (!formulaire || !a || !formulaire.noeud.contains(a)) return null;
    var m = { el: a };
    try { m.debut = a.selectionStart; m.fin = a.selectionEnd; } catch (e) { /* pas de selection */ }
    return m;
  }

  function restaurerFocus(m) {
    if (!m || !document.body.contains(m.el)) return;
    try { m.el.focus({ preventScroll: true }); } catch (e) { m.el.focus(); }
    if (typeof m.debut === "number") {
      try { m.el.setSelectionRange(m.debut, m.fin); } catch (e) { /* champ sans selection */ }
    }
  }

  function puce(libelle, action) {
    var b = el("button", "bibler-puce", libelle);
    b.type = "button";
    b.setAttribute("aria-pressed", "false");
    b.addEventListener("click", action);
    return b;
  }

  function champ(libelle, controle, aide) {
    var l = el("label", "bibler-form-champ");
    var tete = el("span", "bibler-form-libelle", libelle);
    if (aide) tete.appendChild(aide);
    l.appendChild(tete);
    l.appendChild(controle);
    return l;
  }

  function selectPriorite(valeur) {
    var s = el("select", "bibler-form-saisie");
    PRIORITES.forEach(function (p) {
      var o = el("option", null, p.libelle);
      o.value = p.valeur;
      s.appendChild(o);
    });
    s.value = valeur || "information";
    return s;
  }

  function zoneTexte(valeur) {
    var t = el("textarea", "bibler-form-saisie");
    t.maxLength = TEXTE_MAX;
    t.rows = 4;
    t.value = valeur || "";
    var compteur = el("span", "bibler-compteur");
    function maj() { compteur.textContent = t.value.length + "/" + TEXTE_MAX; }
    t.addEventListener("input", maj);
    maj();
    return { saisie: t, compteur: compteur };
  }

  function apresEcriture() {
    charger();
    chargerCompteurConsignes();
  }

  // ------------------------------------------------------------------------
  // Formulaire "Donner une consigne" (dans le panneau du poste)
  // ------------------------------------------------------------------------

  // Bouton, note de publication ou formulaire en cours (le meme noeud DOM est
  // remonte a chaque reaffichage du poste : la saisie survit au rafraichissement).
  function monterActions(actions, f) {
    while (actions.firstChild) actions.removeChild(actions.firstChild);
    if (!peutPublier()) return;
    if (formulaire && formulaire.numero === f.numero) {
      actions.appendChild(formulaire.noeud);
      return;
    }
    if (formulaire) jeterFormulaire();
    if (notePublication && notePublication.numero === f.numero) {
      actions.appendChild(el("div", "bibler-note bibler-note-ok", notePublication.texte));
    }
    var b = el("button", "bibler-btn bibler-btn-principal", "Donner une consigne");
    b.type = "button";
    b.id = "bibler-donner-consigne";
    b.addEventListener("click", function () { ouvrirFormulaire(f); });
    actions.appendChild(b);
  }

  function ciblesDuPoste(f) {
    var st = f.structure || {};
    var liste = [{ cle: "poste", libelle: "Poste " + f.numero, cible: { postes: [f.numero] } }];
    if (st.secteur) liste.push({ cle: "secteur", libelle: "Secteur " + st.secteur, cible: { secteurs: [st.secteur] } });
    if (st.zone) liste.push({ cle: "zone", libelle: "Zone " + st.zone, cible: { zones: [st.zone] } });
    if (st.departement) {
      liste.push({ cle: "departement", libelle: "Département " + st.departement,
                   cible: { departements: [st.departement] } });
    }
    liste.push({ cle: "tout", libelle: "Tout l'événement", cible: { tout_evenement: true } });
    return liste;
  }

  // Shifts du jour de la fiche pas encore termines : "Ce shift"
  function shiftsDuPoste(f) {
    var maintenant = Date.now();
    var auj = new Date().toLocaleDateString("fr-FR", FMT_JOUR);
    var vus = {};
    return (f.vacations || []).filter(function (v) {
      var fin = new Date(v.fin);
      var k = v.debut + "|" + v.fin + "|" + v.format;
      if (!v.debut || isNaN(fin) || fin.getTime() <= maintenant || !RE_FORMAT.test(v.format || "") || vus[k]) return false;
      vus[k] = true;
      return true;
    }).map(function (v) {
      var d = new Date(v.debut);
      var jour = isNaN(d) ? "" : d.toLocaleDateString("fr-FR", FMT_JOUR);
      return {
        debut: isoAvecDecalage(v.debut), fin: isoAvecDecalage(v.fin), format: v.format,
        libelle: "Ce shift (" + v.format + " " + (jour === auj ? "aujourd'hui" : "le " + jour) + ")"
      };
    });
  }

  function cibleFormulaire(fo) {
    var c = fo.cibles.filter(function (x) { return x.cle === fo.cible; })[0] || fo.cibles[0];
    return cibleComplete(c.cible);
  }

  function optionsQuand(fo) {
    var opts = [{ cle: "permanente", libelle: "Permanente", duree: { mode: "permanente" } }];
    fo.shifts.forEach(function (s, i) {
      opts.push({ cle: "shift:" + i, libelle: s.libelle,
                  duree: { mode: "shift", debut: s.debut, fin: s.fin, format: s.format } });
    });
    (fo.formats || []).forEach(function (x) {
      opts.push({ cle: "format:" + x.format, libelle: "Tous les shifts " + x.format,
                  titre: (x.vacations || 0) + " vacation(s)", duree: { mode: "format", format: x.format } });
    });
    return opts;
  }

  function dureeFormulaire(fo) {
    var o = optionsQuand(fo).filter(function (x) { return x.cle === fo.quand; })[0];
    return o ? o.duree : { mode: "permanente" };
  }

  function ouvrirFormulaire(fiche) {
    jeterFormulaire();
    notePublication = null;
    var garde = formulairesGardes[fiche.numero];
    if (garde) {
      // Publication en vol (ou echouee apres que le formulaire a ete quitte) :
      // meme saisie, meme cle d'idempotence.
      delete formulairesGardes[fiche.numero];
      formulaire = garde;
      var act = document.getElementById("bibler-panneau-actions");
      if (act && act.dataset.numero === String(fiche.numero)) monterActions(act, fiche);
      majBoutonPublier(garde);
      if (garde.formats === null) chargerFormats(garde);
      planifierApercu(garde);
      return;
    }
    var fo = {
      numero: fiche.numero,
      cle: nouvelleCle(),           // une cle par ouverture du formulaire
      cibles: ciblesDuPoste(fiche),
      cible: "poste",
      shifts: shiftsDuPoste(fiche),
      quand: "permanente",
      formats: null,                // null = en cours de chargement
      formatsErreur: false,
      enCours: false,
      minuteur: null,
      genApercu: 0,
      genFormats: 0,
      el: {}
    };
    fo.noeud = construireFormulaire(fo);
    formulaire = fo;
    var actions = document.getElementById("bibler-panneau-actions");
    if (actions && actions.dataset.numero === String(fiche.numero)) monterActions(actions, fiche);
    chargerFormats(fo);
    planifierApercu(fo);
    fo.el.titre.focus();
  }

  function jeterFormulaire() {
    var fo = formulaire;
    if (!fo) return;
    formulaire = null;
    if (fo.enCours) formulairesGardes[fo.numero] = fo;   // la cle survit jusqu'a la reponse
    if (fo.minuteur) clearTimeout(fo.minuteur);
    fo.genApercu++;
    fo.genFormats++;
    if (fo.noeud.parentNode) fo.noeud.parentNode.removeChild(fo.noeud);
  }

  function fermerFormulaire() {
    var fo = formulaire;
    jeterFormulaire();
    var actions = document.getElementById("bibler-panneau-actions");
    if (fo && actions && panneau._fiche && actions.dataset.numero === String(fo.numero)) {
      monterActions(actions, panneau._fiche);
      var b = document.getElementById("bibler-donner-consigne");
      if (b) b.focus();
    }
  }

  function construireFormulaire(fo) {
    var form = el("form", "bibler-form");
    form.id = "bibler-form-consigne";
    form.noValidate = true;
    form.addEventListener("submit", function (e) { e.preventDefault(); publier(fo); });
    form.appendChild(el("h4", "bibler-section-titre", "Donner une consigne"));

    // 1. A qui
    form.appendChild(el("div", "bibler-form-libelle", "À qui"));
    var pc = el("div", "bibler-puces");
    pc.setAttribute("role", "group");
    pc.setAttribute("aria-label", "À qui");
    fo.cibles.forEach(function (c) {
      var b = puce(c.libelle, function () {
        if (fo.cible === c.cle) return;
        fo.cible = c.cle;
        majPucesCible(fo);
        chargerFormats(fo);
        planifierApercu(fo);
      });
      b.dataset.cible = c.cle;
      pc.appendChild(b);
    });
    fo.el.cibles = pc;
    form.appendChild(pc);
    majPucesCible(fo);

    // 2. Quand
    form.appendChild(el("div", "bibler-form-libelle", "Quand"));
    var pq = el("div", "bibler-puces");
    pq.setAttribute("role", "group");
    pq.setAttribute("aria-label", "Quand");
    fo.el.quand = pq;
    form.appendChild(pq);
    rendrePucesQuand(fo);

    // 3. Titre, texte, priorite
    var titre = el("input", "bibler-form-saisie");
    titre.type = "text";
    titre.maxLength = TITRE_MAX;
    titre.name = "titre";
    titre.setAttribute("autocomplete", "off");
    fo.el.titre = titre;
    form.appendChild(champ("Titre", titre));
    var zt = zoneTexte("");
    zt.saisie.name = "texte";
    fo.el.texte = zt.saisie;
    form.appendChild(champ("Texte", zt.saisie, zt.compteur));
    var prio = selectPriorite("information");
    prio.name = "priorite";
    fo.el.priorite = prio;
    form.appendChild(champ("Priorité", prio));

    // Options repliees : question de controle, dans le briefing
    var opt = el("details", "bibler-details bibler-form-options");
    opt.appendChild(el("summary", null, "Options"));
    var q = el("input", "bibler-form-saisie");
    q.type = "text";
    q.maxLength = 300;
    fo.el.question = q;
    opt.appendChild(champ("Question de contrôle", q));
    var bonne = el("input", "bibler-form-saisie");
    bonne.type = "text";
    bonne.maxLength = 200;
    fo.el.bonne = bonne;
    opt.appendChild(champ("Bonne réponse", bonne));
    fo.el.mauvaises = [];
    for (var i = 1; i <= 3; i++) {
      var m = el("input", "bibler-form-saisie");
      m.type = "text";
      m.maxLength = 200;
      fo.el.mauvaises.push(m);
      opt.appendChild(champ("Mauvaise réponse " + i + (i === 3 ? " (facultative)" : ""), m));
    }
    var lb = el("label", "bibler-form-case");
    var briefing = el("input");
    briefing.type = "checkbox";
    briefing.checked = true;
    fo.el.briefing = briefing;
    lb.appendChild(briefing);
    lb.appendChild(document.createTextNode(" Dans le briefing"));
    opt.appendChild(lb);
    fo.el.options = opt;
    form.appendChild(opt);

    // 4. Apercu, rappel, envoi
    fo.el.apercu = el("div", "bibler-apercu");
    fo.el.apercu.setAttribute("aria-live", "polite");
    form.appendChild(fo.el.apercu);
    form.appendChild(el("div", "bibler-rappel", "Pas de nom, de téléphone ni d'e-mail dans la consigne"));
    fo.el.message = el("div", "bibler-note");
    fo.el.message.setAttribute("role", "alert");
    fo.el.message.hidden = true;
    form.appendChild(fo.el.message);
    var boutons = el("div", "bibler-form-boutons");
    var envoyer = el("button", "bibler-btn bibler-btn-principal", "Publier");
    envoyer.type = "submit";
    envoyer.id = "bibler-publier";
    fo.el.publier = envoyer;
    var annuler = el("button", "bibler-btn", "Annuler");
    annuler.type = "button";
    annuler.addEventListener("click", function () {
      if (formulaire === fo && !fo.enCours) fermerFormulaire();
    });
    fo.el.annuler = annuler;
    boutons.appendChild(envoyer);
    boutons.appendChild(annuler);
    form.appendChild(boutons);
    return form;
  }

  function majPucesCible(fo) {
    Array.prototype.forEach.call(fo.el.cibles.children, function (b) {
      var actif = b.dataset.cible === fo.cible;
      b.classList.toggle("active", actif);
      b.setAttribute("aria-pressed", actif ? "true" : "false");
    });
  }

  function rendrePucesQuand(fo) {
    var pq = fo.el.quand;
    while (pq.firstChild) pq.removeChild(pq.firstChild);
    var opts = optionsQuand(fo);
    if (!opts.some(function (o) { return o.cle === fo.quand; })) fo.quand = "permanente";
    opts.forEach(function (o) {
      var b = puce(o.libelle, function () {
        if (fo.quand === o.cle) return;
        fo.quand = o.cle;
        rendrePucesQuand(fo);
        planifierApercu(fo);
      });
      b.dataset.quand = o.cle;
      if (o.titre) b.title = o.titre;
      var actif = o.cle === fo.quand;
      b.classList.toggle("active", actif);
      b.setAttribute("aria-pressed", actif ? "true" : "false");
      pq.appendChild(b);
    });
    if (fo.formats === null) pq.appendChild(el("span", "bibler-puces-info", "formats de shift..."));
    else if (fo.formatsErreur) pq.appendChild(el("span", "bibler-puces-info", "formats de shift indisponibles"));
  }

  // Formats de shift de la cible choisie (GET /api/bibler/formats)
  function chargerFormats(fo) {
    var gen = ++fo.genFormats;
    fetch("/api/bibler/formats?" + parametresCible(cibleFormulaire(fo)), { credentials: "same-origin" })
      .then(lireJson)
      .then(function (rep) {
        if (formulaire !== fo || gen !== fo.genFormats) return;
        var liste = rep.statut === 200 ? (Array.isArray(rep.corps) ? rep.corps : rep.corps.formats) : null;
        fo.formatsErreur = !Array.isArray(liste);
        fo.formats = (liste || []).filter(function (x) { return x && RE_FORMAT.test(x.format || ""); });
        var avant = fo.quand;
        rendrePucesQuand(fo);
        if (fo.quand !== avant) planifierApercu(fo);
      })
      .catch(function () {
        if (formulaire !== fo || gen !== fo.genFormats) return;
        fo.formatsErreur = true;
        fo.formats = [];
        var avant = fo.quand;
        rendrePucesQuand(fo);
        if (fo.quand !== avant) planifierApercu(fo);
      });
  }

  function planifierApercu(fo) {
    if (fo.minuteur) clearTimeout(fo.minuteur);
    fo.genApercu++;
    fo.el.apercu.textContent = "Calcul des destinataires...";
    fo.minuteur = setTimeout(function () { fo.minuteur = null; apercu(fo); }, APERCU_MS);
  }

  function apercu(fo) {
    var gen = ++fo.genApercu;
    var d = dureeFormulaire(fo);
    var p = parametresCible(cibleFormulaire(fo));
    if (d.mode !== "permanente") p += "&formats=" + encodeURIComponent(d.format);
    if (d.mode === "shift") p += "&debut=" + encodeURIComponent(d.debut) + "&fin=" + encodeURIComponent(d.fin);
    fetch("/api/bibler/consignes/apercu?" + p, { credentials: "same-origin" })
      .then(lireJson)
      .then(function (rep) {
        if (formulaire !== fo || gen !== fo.genApercu) return;
        var n = rep.statut === 200 && rep.corps ? rep.corps.encadrants : undefined;
        if (typeof n === "number") {
          fo.el.apercu.textContent = "Environ " + n + (n > 1 ? " encadrants concernés" : " encadrant concerné");
        } else {
          fo.el.apercu.textContent = "Aperçu indisponible : " + erreurDe(rep).texte;
        }
      })
      .catch(function () {
        if (formulaire !== fo || gen !== fo.genApercu) return;
        fo.el.apercu.textContent = "Aperçu indisponible : Bibler indisponible";
      });
  }

  function messageFormulaire(fo, texte, erreur) {
    fo.el.message.textContent = texte || "";
    fo.el.message.hidden = !texte;
    fo.el.message.classList.toggle("bibler-note-erreur", !!erreur);
  }

  // Question de contrôle : null (aucune), objet, ou false (incomplete)
  function questionControle(fo) {
    var question = fo.el.question.value.trim();
    var bonne = fo.el.bonne.value.trim();
    var mauvaises = fo.el.mauvaises.map(function (m) { return m.value.trim(); }).filter(Boolean);
    if (!question && !bonne && !mauvaises.length) return null;
    if (!question || !bonne || mauvaises.length < 2) {
      fo.el.options.open = true;
      messageFormulaire(fo, "La question de contrôle demande la question, la bonne réponse et 2 ou 3 " +
        "mauvaises réponses (ou videz ces champs).", true);
      return false;
    }
    return { question: question, bonne: bonne, mauvaises: mauvaises };
  }

  function majBoutonPublier(fo) {
    fo.el.publier.disabled = fo.enCours;
    fo.el.publier.textContent = fo.enCours ? "Publication..." : "Publier";
    if (fo.el.annuler) fo.el.annuler.disabled = fo.enCours;
  }

  function publier(fo) {
    if (fo.enCours || formulaire !== fo) return;   // double clic : une seule requete
    var titre = fo.el.titre.value.trim();
    if (!titre) {
      messageFormulaire(fo, "Donnez un titre à la consigne.", true);
      fo.el.titre.focus();
      return;
    }
    var question = questionControle(fo);
    if (question === false) return;
    var d = dureeFormulaire(fo);
    var duree = { mode: d.mode };
    if (d.mode === "shift") { duree.debut = d.debut; duree.fin = d.fin; }
    if (d.mode !== "permanente") duree.format = d.format;
    var corps = {
      event: window.selectedEvent,
      year: window.selectedYear,
      titre: titre,
      texte: fo.el.texte.value.trim(),
      priorite: fo.el.priorite.value,
      cible: cibleFormulaire(fo),
      duree: duree,
      dans_briefing: fo.el.briefing.checked
    };
    if (question) corps.question_controle = question;
    fo.enCours = true;
    majBoutonPublier(fo);
    messageFormulaire(fo, "", false);
    ecrire("POST", "/api/bibler/consignes", corps, { "X-Idempotency-Key": fo.cle }).then(function (rep) {
      fo.enCours = false;
      var ok = (rep.statut === 200 || rep.statut === 201) && rep.corps && rep.corps.ok !== false;
      if (ok && formulairesGardes[fo.numero] === fo) delete formulairesGardes[fo.numero];
      if (ok) {
        var c = rep.corps.consigne || {};
        var etaitOuvert = formulaire === fo;
        if (etaitOuvert) jeterFormulaire();   // la prochaine ouverture aura une nouvelle cle
        notePublication = { numero: fo.numero, texte: "Consigne publiée : " + (c.titre || titre) +
          (c.cible_libelle ? " (" + c.cible_libelle + ")" : "") };
        var actions = document.getElementById("bibler-panneau-actions");
        if (etaitOuvert && actions && panneau._fiche && actions.dataset.numero === String(fo.numero)) {
          monterActions(actions, panneau._fiche);
        }
        apresEcriture();
        return;
      }
      // Echec : le formulaire (garde ou affiche) garde sa cle et montre l'erreur ;
      // un formulaire garde la retrouvera a sa reouverture sur ce poste.
      majBoutonPublier(fo);
      var m = messageEcriture(rep, true);
      messageFormulaire(fo, m.texte, true);
      if (formulaire !== fo) return;
      if (m.recharger) apresEcriture();
    });
  }

  // ------------------------------------------------------------------------
  // Liste "Consignes (n)" de la barre
  // ------------------------------------------------------------------------

  function basculerListeConsignes() {
    if (listeConsignes.classList.contains("open")) { fermerListeConsignes(); return; }
    listeConsignes.classList.add("open");
    btnConsignes.setAttribute("aria-expanded", "true");
    rendreListeConsignes();
    chargerCompteurConsignes();
  }

  function fermerListeConsignes() {
    if (!listeConsignes) return;
    listeConsignes.classList.remove("open");
    if (btnConsignes) btnConsignes.setAttribute("aria-expanded", "false");
  }

  function rendreListeConsignes() {
    if (!listeConsignes || !listeConsignes.classList.contains("open")) return;
    while (listeConsignes.firstChild) listeConsignes.removeChild(listeConsignes.firstChild);
    if (!consignesListe) {
      listeConsignes.appendChild(el("li", "bibler-suggestion-vide", consignesErreur || "Chargement..."));
      return;
    }
    var liste = consignesEnVigueur();
    if (consignesErreur) listeConsignes.appendChild(el("li", "bibler-suggestion-vide", consignesErreur));
    if (!liste.length) {
      listeConsignes.appendChild(el("li", "bibler-suggestion-vide", "Aucune consigne en vigueur"));
      return;
    }
    liste.forEach(function (c) {
      var li = el("li", "bibler-ligne-consigne");
      li.setAttribute("role", "option");
      if (surlignage && surlignage.id === c.id) li.classList.add("active");
      var k = prioConsigne(c);
      var b = el("button", "bibler-ligne-btn bibler-prio-bord-" + k);
      b.type = "button";
      b.dataset.consigne = c.id;
      var t = el("span", "bibler-consigne-titre");
      t.appendChild(iconePrio(k));
      t.appendChild(el("span", "bibler-consigne-titre-texte", c.titre || ""));
      t.appendChild(pucePrio(c, k));
      b.appendChild(t);
      if (c.cible_libelle) b.appendChild(el("span", "bibler-consigne-meta", c.cible_libelle));
      var s = c.suivi;
      if (s) {
        var taux = el("span", "bibler-ligne-taux" + ((s.part_lue || 0) >= 100 ? " bibler-jauge-pleine" : ""));
        var fond = el("span", "bibler-jauge-fond");
        var plein = el("span", "bibler-jauge-plein");
        plein.style.width = Math.max(0, Math.min(100, Number(s.part_lue) || 0)) + "%";
        fond.appendChild(plein);
        taux.appendChild(fond);
        taux.appendChild(el("span", "bibler-consigne-taux", "Lu " + (s.part_lue || 0) + " % - transmis " +
          (s.part_transmise || 0) + " %" + (s.total ? " - " + s.total + " encadrant(s)" : "")));
        b.appendChild(taux);
      }
      b.addEventListener("click", function () { surlignerConsigne(c.id); });
      li.appendChild(b);
      listeConsignes.appendChild(li);
    });
  }

  // ------------------------------------------------------------------------
  // Consigne : surlignage des postes cibles, detail, suivi, modifier, lever
  // ------------------------------------------------------------------------

  function surlignerConsigne(id) {
    id = String(id || "");
    if (!RE_ID.test(id) || !map) return;
    if (!actif) activer();
    if (!panneau) return;
    fermerListeConsignes();
    var connue = (consignesListe || []).filter(function (c) { return c.id === id; })[0];
    surlignage = { id: id, cible: connue ? connue.cible || {} : null, ajuste: false,
                   prio: connue ? prioConsigne(connue) : "information" };
    posteOuvert = null;
    genFiche++;
    consigneOuverte = id;
    rendre();
    ajusterSurlignage();
    viderPanneau();
    panneau._majCibles = null;
    panneau.appendChild(enteteSimple("Consigne"));
    panneau.appendChild(el("div", "bibler-panneau-info", "Chargement..."));
    panneau.hidden = false;
    chargerDetail(id, "");
  }

  // Cadrage sur les postes cibles, une fois par surlignage (quand la cible
  // et la liste des postes sont connues)
  function ajusterSurlignage() {
    if (!surlignage || !surlignage.cible || surlignage.ajuste || !donnees || !map) return;
    surlignage.ajuste = true;
    var pts = donnees.postes.filter(function (p) { return p.coords && estCible(p, surlignage.cible); })
      .map(function (p) { return [p.coords.lat, p.coords.lon]; });
    if (!pts.length) return;
    var large = map.getContainer().clientWidth > 700;
    map.fitBounds(L.latLngBounds(pts), {
      paddingTopLeft: [40, 90],
      paddingBottomRight: [large ? 410 : 40, large ? 40 : 240],
      maxZoom: 17
    });
  }

  function chargerDetail(id, note) {
    var gen = ++genDetail;
    fetch("/api/bibler/consignes/" + encodeURIComponent(id), { credentials: "same-origin" })
      .then(lireJson)
      .then(function (rep) {
        if (gen !== genDetail || consigneOuverte !== id) return;
        if (rep.statut === 200 && rep.corps && rep.corps.consigne) {
          var c = rep.corps.consigne;
          if (surlignage && surlignage.id === id) {
            surlignage.cible = c.cible || {};
            surlignage.prio = prioConsigne(c);
            rendre();
            ajusterSurlignage();
          }
          afficherConsigne(c, rep.corps.suivi || null, note);
        } else {
          afficherErreurConsigne(erreurDe(rep).texte);
        }
      })
      .catch(function () {
        if (gen !== genDetail || consigneOuverte !== id) return;
        afficherErreurConsigne("Bibler indisponible");
      });
  }

  function afficherErreurConsigne(texte) {
    viderPanneau();
    panneau._majCibles = null;
    panneau.appendChild(enteteSimple("Consigne"));
    panneau.appendChild(el("div", "bibler-panneau-info bibler-panneau-erreur", texte));
    panneau.hidden = false;
  }

  function libellePeriode(c) {
    if (!c.debut && !c.fin) return "";
    var t = c.debut ? "Du " + heureEtJour(c.debut) : "Depuis la publication";
    return t + (c.fin ? " au " + heureEtJour(c.fin) : ", jusqu'à la levée");
  }

  function afficherConsigne(c, suivi, note) {
    viderPanneau();
    var k = prioConsigne(c);
    // Bandeau a la couleur de la priorite (information, important, urgent)
    var entete = enteteSimple("Consigne - " + (c.priorite_libelle || PRIORITES_CONSIGNE[k].libelle));
    entete.classList.add("bibler-entete-consigne", "bibler-entete-" + k);
    entete.insertBefore(icone(PRIORITES_CONSIGNE[k].icone), entete.firstChild);
    panneau.appendChild(entete);

    var tete = el("div", "bibler-panneau-tete bibler-consigne-tete");
    tete.appendChild(el("div", "bibler-consigne-grand-titre", c.titre || ""));
    var ligne = el("div", "bibler-etat-ligne");
    ligne.appendChild(el("span", "bibler-etat-consigne bibler-etat-consigne-" +
      String(c.etat || "").replace(/[^a-z_]/g, ""), LIBELLES_ETAT_CONSIGNE[c.etat] || c.etat || ""));
    if (c.dans_briefing) ligne.appendChild(el("span", "bibler-tag-reel", "Dans le briefing"));
    if (c.modifiee) ligne.appendChild(el("span", "bibler-tag-reel", "version " + c.version));
    tete.appendChild(ligne);
    panneau.appendChild(tete);

    var corps = el("div", "bibler-panneau-corps");
    // Le message d'abord, en grand : c'est ce que l'encadrant recoit
    if (c.texte) corps.appendChild(el("div", "bibler-consigne-message bibler-prio-bord-" + k, c.texte));

    var infos = el("div", "bibler-infos");
    if (c.cible_libelle) infos.appendChild(ligneInfo("groups", c.cible_libelle));
    var cibles = ligneInfo("my_location", "");
    cibles.classList.add("bibler-info-cibles");
    var ciblesTexte = cibles.lastChild;
    infos.appendChild(cibles);
    var periode = libellePeriode(c);
    if (periode) infos.appendChild(ligneInfo("schedule", periode));
    if (c.etat === "levee" && c.levee_heure) infos.appendChild(ligneInfo("block", "Levée à " + c.levee_heure));
    var meta = [c.auteur, c.heure].filter(Boolean).join(" - ");
    if (meta) infos.appendChild(ligneInfo("person", "Par " + meta));
    corps.appendChild(infos);

    // Nombre de postes surlignes (mis a jour a chaque rechargement de la couche)
    panneau._majCibles = function () {
      if (!donnees) { ciblesTexte.textContent = "Postes non chargés"; return; }
      var vises = donnees.postes.filter(function (p) { return estCible(p, c.cible); });
      var sans = vises.filter(function (p) { return !p.coords; }).length;
      ciblesTexte.textContent = vises.length + " poste(s) en surbrillance sur la carte" +
        (sans ? ", " + sans + " sans position" : "");
    };
    panneau._majCibles();

    var qc = c.question_controle;
    if (qc && qc.question) {
      var dq = el("details", "bibler-details bibler-question");
      var sq = el("summary");
      sq.appendChild(icone("quiz"));
      sq.appendChild(document.createTextNode(" Question de contrôle"));
      dq.appendChild(sq);
      dq.appendChild(el("p", null, qc.question));
      var ulq = el("ul");
      if (qc.bonne) ulq.appendChild(el("li", "bibler-bonne", qc.bonne + " (bonne réponse)"));
      (qc.mauvaises || qc.choix || []).forEach(function (m) { ulq.appendChild(el("li", null, m)); });
      dq.appendChild(ulq);
      corps.appendChild(dq);
    }

    corps.appendChild(sectionSuivi(suivi));

    var actions = el("div", "bibler-panneau-actions bibler-consigne-actions");
    actions.id = "bibler-consigne-actions";
    corps.appendChild(actions);
    panneau.appendChild(corps);
    panneau.hidden = false;
    rendreActionsConsigne(actions, c, { mode: "aucun", note: note || "" });
  }

  function sectionSuivi(suivi) {
    var ss = section("Accusés de réception");
    if (!suivi) {
      ss.appendChild(el("div", "bibler-vide", "Suivi indisponible"));
      return ss;
    }
    var total = suivi.total || 0;
    ss.appendChild(jauge("Lu par les encadrants", suivi.lus, total, suivi.part_lue));
    ss.appendChild(jauge("Transmis aux agents", suivi.transmis, total, suivi.part_transmise));
    if (suivi.mauvaises_reponses) {
      ss.appendChild(el("div", "bibler-note bibler-note-erreur", suivi.mauvaises_reponses + " mauvaise(s) réponse(s) " +
        "à la question de contrôle"));
    }
    var lignes = (suivi.lignes || []).map(function (l, i) { return { l: l, i: i }; });
    if (!lignes.length) {
      ss.appendChild(el("div", "bibler-vide", "Aucun encadrant concerné"));
      return ss;
    }
    // Qui manque d'abord
    lignes.sort(function (a, b) { return (a.l.lu ? 1 : 0) - (b.l.lu ? 1 : 0) || a.i - b.i; });
    var ul = el("ul", "bibler-suivi");
    lignes.forEach(function (x) {
      var l = x.l;
      var li = el("li", l.lu ? "" : "bibler-suivi-manque");
      var ico = icone(l.lu ? "check_circle" : "radio_button_unchecked");
      ico.classList.add("bibler-suivi-ico");
      li.appendChild(ico);
      var gauche = el("span", "bibler-suivi-qui");
      gauche.appendChild(el("span", "bibler-agent-nom", l.nom || "?"));
      gauche.appendChild(el("small", null, l.cible === false ? "hors cible" : (l.perimetre || "")));
      li.appendChild(gauche);
      var etats = el("span", "bibler-suivi-etats");
      if (!l.lu) etats.appendChild(el("span", "bibler-statut bibler-statut-manque", "Non lu"));
      if (l.lu) etats.appendChild(el("span", "bibler-statut", "Lu " + l.lu));
      if (l.valide) etats.appendChild(el("span", "bibler-statut bibler-statut-ok", "Validé " + l.valide));
      if (l.transmis) {
        etats.appendChild(el("span", "bibler-statut bibler-statut-ok", "Transmis " + l.transmis +
          (l.agents_informes ? " (" + l.agents_informes + " agent(s))" : "")));
      }
      if (l.mauvaises_reponses) {
        etats.appendChild(el("span", "bibler-statut bibler-statut-manque", l.mauvaises_reponses + " mauvaise(s)"));
      }
      li.appendChild(etats);
      ul.appendChild(li);
    });
    ss.appendChild(ul);
    return ss;
  }

  // Actions d'une consigne : aucun (boutons), modifier (formulaire), lever
  // (confirmation dans le panneau, jamais de confirm()).
  function rendreActionsConsigne(zone, c, st) {
    while (zone.firstChild) zone.removeChild(zone.firstChild);
    if (st.note) zone.appendChild(el("div", "bibler-note" + (st.erreur ? " bibler-note-erreur" : " bibler-note-ok"), st.note));
    if (!peutPublier() || c.etat === "levee") return;
    function changer(mode) { rendreActionsConsigne(zone, c, { mode: mode }); }

    if (st.mode === "modifier") {
      var form = el("form", "bibler-form");
      form.noValidate = true;
      var titre = el("input", "bibler-form-saisie");
      titre.type = "text";
      titre.maxLength = TITRE_MAX;
      titre.value = st.titre !== undefined ? st.titre : (c.titre || "");
      form.appendChild(champ("Titre", titre));
      var zt = zoneTexte(st.texte !== undefined ? st.texte : (c.texte || ""));
      form.appendChild(champ("Texte", zt.saisie, zt.compteur));
      var prio = selectPriorite(st.priorite || c.priorite);
      form.appendChild(champ("Priorité", prio));
      form.appendChild(el("div", "bibler-rappel", "Pas de nom, de téléphone ni d'e-mail dans la consigne"));
      var msg = el("div", "bibler-note bibler-note-erreur");
      msg.setAttribute("role", "alert");
      msg.hidden = true;
      form.appendChild(msg);
      var boutons = el("div", "bibler-form-boutons");
      var ok = el("button", "bibler-btn bibler-btn-principal", "Enregistrer");
      ok.type = "submit";
      var non = el("button", "bibler-btn", "Annuler");
      non.type = "button";
      non.addEventListener("click", function () { changer("aucun"); });
      boutons.appendChild(ok);
      boutons.appendChild(non);
      form.appendChild(boutons);
      var enCours = false;
      form.addEventListener("submit", function (e) {
        e.preventDefault();
        if (enCours) return;
        if (!titre.value.trim()) { msg.textContent = "Donnez un titre à la consigne."; msg.hidden = false; return; }
        enCours = true;
        ok.disabled = true;
        ok.textContent = "Enregistrement...";
        var corps = { titre: titre.value.trim(), texte: zt.saisie.value.trim(), priorite: prio.value, version: c.version };
        ecrire("PATCH", "/api/bibler/consignes/" + encodeURIComponent(c.id), corps).then(function (rep) {
          if (consigneOuverte !== c.id) return;
          if (rep.statut === 200 && rep.corps && rep.corps.ok !== false) {
            chargerDetail(c.id, "Consigne modifiée");
            apresEcriture();
            return;
          }
          var m = messageEcriture(rep, false);
          if (m.recharger) {
            chargerDetail(c.id, m.texte);
            apresEcriture();
            return;
          }
          enCours = false;
          ok.disabled = false;
          ok.textContent = "Enregistrer";
          msg.textContent = m.texte;
          msg.hidden = false;
        });
      });
      zone.appendChild(form);
      titre.focus();
      return;
    }

    if (st.mode === "lever") {
      var conf = el("div", "bibler-confirmation");
      conf.appendChild(el("div", null, "Lever cette consigne ? Elle ne sera plus diffusée aux encadrants."));
      var msgL = el("div", "bibler-note bibler-note-erreur");
      msgL.setAttribute("role", "alert");
      msgL.hidden = true;
      conf.appendChild(msgL);
      var bl = el("div", "bibler-form-boutons");
      var oui = el("button", "bibler-btn bibler-btn-danger", "Confirmer la levée");
      oui.type = "button";
      oui.id = "bibler-confirmer-levee";
      var annuler = el("button", "bibler-btn", "Annuler");
      annuler.type = "button";
      annuler.addEventListener("click", function () { changer("aucun"); });
      var enCoursL = false;
      oui.addEventListener("click", function () {
        if (enCoursL) return;
        enCoursL = true;
        oui.disabled = true;
        oui.textContent = "Levée...";
        ecrire("POST", "/api/bibler/consignes/" + encodeURIComponent(c.id) + "/lever", {}).then(function (rep) {
          if (consigneOuverte !== c.id) return;
          if (rep.statut === 200 && rep.corps && rep.corps.ok !== false) {
            chargerDetail(c.id, "Consigne levée");
            apresEcriture();
            return;
          }
          var m = messageEcriture(rep, false);
          if (m.recharger) {
            chargerDetail(c.id, m.texte);
            apresEcriture();
            return;
          }
          enCoursL = false;
          oui.disabled = false;
          oui.textContent = "Confirmer la levée";
          msgL.textContent = m.texte;
          msgL.hidden = false;
        });
      });
      bl.appendChild(oui);
      bl.appendChild(annuler);
      conf.appendChild(bl);
      zone.appendChild(conf);
      annuler.focus();
      return;
    }

    var b = el("div", "bibler-form-boutons");
    var bm = el("button", "bibler-btn", "Modifier");
    bm.type = "button";
    bm.id = "bibler-modifier-consigne";
    bm.addEventListener("click", function () { changer("modifier"); });
    var bv = el("button", "bibler-btn bibler-btn-danger", "Lever");
    bv.type = "button";
    bv.id = "bibler-lever-consigne";
    bv.addEventListener("click", function () { changer("lever"); });
    b.appendChild(bm);
    b.appendChild(bv);
    zone.appendChild(b);
  }

  // ------------------------------------------------------------------------
  // Cycle de vie
  // ------------------------------------------------------------------------

  function demarrerTimer() {
    arreterTimer();
    if (!direct) return;
    timer = setInterval(function () {
      if (document.hidden || !visible()) return;
      verifierCle();
      charger();
    }, DIRECT_MS);
  }

  function arreterTimer() {
    if (timer) { clearInterval(timer); timer = null; }
  }

  function visible() {
    var v = window.CockpitMapView;
    return !v || !v.currentView || v.currentView() === "map";
  }

  // Changement d'evenement ou d'annee : rechargement complet
  function verifierCle() {
    if (!actif || !cleVue || cleCourante() === cleVue) return false;
    donnees = null;
    cleChargee = "";
    grise = false;
    erreurCode = "";
    consignesListe = null;
    consignesErreur = "";
    fermerListeConsignes();
    fermerPanneau();
    majCompteurConsignes(null);
    return true;
  }

  function surRechargementCarte() {
    if (!actif) return;
    var change = verifierCle();
    if (change || (direct && Date.now() - dernierChargement > DIRECT_MS)) charger();
  }

  function surVisibilite() {
    if (!actif || !direct || document.hidden || !visible()) return;
    if (Date.now() - dernierChargement > DIRECT_MS) charger();
  }

  function activer() {
    if (actif || !map) return;
    actif = true;
    if (ctrlBtn) ctrlBtn.classList.add("active");
    if (!couche) couche = L.layerGroup();
    couche.addTo(map);
    if (!haut) construireBarre();
    if (!panneau) construirePanneau();
    haut.hidden = false;
    map.on("zoomend", rendre);
    donnees = null;
    cleChargee = "";
    cleVue = "";
    grise = false;
    erreurCode = "";
    majCompteurConsignes(null);
    charger();
    if (direct) demarrerTimer();
  }

  function desactiver() {
    if (!actif) return;
    actif = false;
    genListe++;
    arreterTimer();
    if (ctrlBtn) ctrlBtn.classList.remove("active");
    map.off("zoomend", rendre);
    if (couche) { couche.clearLayers(); map.removeLayer(couche); }
    if (haut) haut.hidden = true;
    cleChargee = "";
    cleVue = "";
    donnees = null;
    consignesListe = null;
    consignesErreur = "";
    fermerSuggestions();
    fermerListeConsignes();
    fermerPanneau();
  }

  function initialiser() {
    if (map) return true;
    map = window.CockpitMapView && window.CockpitMapView.getMap && window.CockpitMapView.getMap();
    if (!map) return false;
    ajouterControle();
    document.addEventListener("cockpit:map-reload", surRechargementCarte);
    document.addEventListener("visibilitychange", surVisibilite);
    return true;
  }

  window.CockpitBibler = {
    activer: activer,
    desactiver: desactiver,
    ouvrirPoste: function (numero) { ouvrirPoste(numero, false); },
    surlignerConsigne: function (id) {
      // Depuis une autre vue (lien direct, page Terrain) : bascule sur la carte
      var v = window.CockpitMapView;
      if (v && v.switchView && v.currentView && v.currentView() !== "map") v.switchView("map");
      initialiser();
      surlignerConsigne(id);
    }
  };

  // La carte est creee a la premiere ouverture de la vue carte (initMap de
  // map_view.js, synchrone, suivi de loadEventMarkers qui emet
  // "cockpit:map-reload") : verification immediate, sinon au premier signal.
  function attendreCarte() {
    if (typeof L === "undefined") return;
    if (initialiser()) return;
    function auSignal() {
      if (initialiser()) document.removeEventListener("cockpit:map-reload", auSignal);
    }
    document.addEventListener("cockpit:map-reload", auSignal);
  }

  // Lien direct /?bibler=consigne:<id> (page Terrain "Voir sur la carte") :
  // attend l'evenement et l'annee de l'accueil, bascule sur la carte, active
  // la couche et surligne la cible. Le parametre est retire de l'adresse pour
  // qu'un rechargement ne le rejoue pas.
  function lienDirect() {
    var v = null;
    try { v = new URLSearchParams(location.search).get("bibler"); } catch (e) { v = null; }
    if (!v) return;
    try {
      var u = new URL(location.href);
      u.searchParams.delete("bibler");
      history.replaceState(null, "", u.pathname + (u.search || "") + u.hash);
    } catch (e) { /* adresse inchangee */ }
    var m = /^consigne:([0-9a-fA-F]{24})$/.exec(v);
    if (!m) return;
    var essais = 0;
    (function attendre() {
      if (window.selectedEvent && window.selectedYear && window.CockpitMapView && typeof L !== "undefined") {
        window.CockpitBibler.surlignerConsigne(m[1]);
        return;
      }
      if (++essais < 100) setTimeout(attendre, 300);
    })();
  }

  function demarrer() {
    attendreCarte();
    lienDirect();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", demarrer);
  else demarrer();
})();
