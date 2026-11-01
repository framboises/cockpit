// ==========================================================================
// BIBLER CARTE - couche "Personnels en poste" sur la carte d'accueil
// (spec 2026-11-01-pont-bibler-consignes, section 4.3)
//
// Lecture seule : postes Bibler (etat, effectif, consignes en vigueur) lus par
// le relais de Cockpit (/api/bibler/*, bibler_postes.py), jamais Bibler en
// direct. Toute donnee Bibler est inseree par textContent / API DOM (jamais
// innerHTML avec des donnees).
//
// Expose : window.CockpitBibler = { activer, desactiver, ouvrirPoste(numero),
// surlignerConsigne(id) }. Le panneau lateral vit dans #bibler-panneau ; la
// zone #bibler-panneau-actions recevra le formulaire "Donner une consigne"
// (Task 9), le bouton "Consignes (n)" de la barre la liste des consignes.
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
    a_venir: "A venir"
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
    if (rep.statut === 502 || rep.statut === 503 || code === "bibler_indisponible" ||
        code === "bibler_non_configure") {
      return { code: "indisponible", texte: "Bibler indisponible" };
    }
    if (code === "evenement_inconnu") {
      return { code: "evenement_inconnu", texte: "Evenement inconnu de Bibler" };
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
    btnDirect.title = "Presence en direct, actualisee chaque minute";
    btnDirect.addEventListener("click", passerEnDirect);
    champHeure = el("input", "bibler-heure");
    champHeure.type = "datetime-local";
    champHeure.title = "Jour et heure (passe : presence reelle ; futur : prevu)";
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

    // Rempli par Task 9 (liste des consignes en vigueur) ; inerte ici.
    btnConsignes = el("button", "bibler-btn bibler-consignes-btn", "Consignes");
    btnConsignes.type = "button";
    btnConsignes.id = "bibler-consignes-btn";
    btnConsignes.disabled = true;

    barre.appendChild(temps);
    barre.appendChild(recherche);
    barre.appendChild(btnConsignes);

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
        if (!Array.isArray(liste)) { majCompteurConsignes(null); return; }
        majCompteurConsignes(liste.filter(function (c) { return c && c.etat === "en_vigueur"; }).length);
      })
      .catch(function () { if (gen === genConsignes) majCompteurConsignes(null); });
  }

  // Bandeau : erreur, ou mode de la vue figee ("Presence reelle a" / "Prevu a")
  function majBandeau() {
    if (!bandeau) return;
    bandeau.className = "bibler-bandeau";
    var texte = "";
    if (erreurCode === "indisponible") {
      texte = donnees ? "Bibler indisponible : derniere situation connue, grisee" : "Bibler indisponible";
      bandeau.classList.add("bibler-bandeau-erreur");
    } else if (erreurCode) {
      texte = erreurCode;
      bandeau.classList.add("bibler-bandeau-erreur");
    } else if (donnees && donnees.mode === "prevu") {
      texte = "Prevu a " + heureEtJour(donnees.instant) +
        " : planning seulement, aucune presence constatee (vert = prevu)";
      bandeau.classList.add("bibler-bandeau-prevu");
    } else if (donnees && !donnees.direct) {
      texte = "Presence reelle a " + heureEtJour(donnees.instant);
      bandeau.classList.add("bibler-bandeau-reel");
    }
    var sans = donnees ? donnees.postes.filter(function (p) { return !p.coords; }).length : 0;
    if (texte && sans && !erreurCode) texte += " - " + sans + " poste(s) sans position (recherche)";
    bandeau.textContent = texte;
    bandeau.hidden = !texte;
    if (btnDirect && donnees && donnees.direct && !erreurCode) {
      btnDirect.title = "Presence en direct, actualisee chaque minute (maj " + heure(donnees.instant) + ")";
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
        majBandeau();
        synchroniserPanneau();
      })
      .catch(function () {
        if (gen !== genListe || !actif) return;
        traiterErreurListe({ code: "indisponible", texte: "Bibler indisponible" }, cle);
        rendre();
        majBandeau();
        synchroniserPanneau();
      });
  }

  // Le panneau suit toujours la liste affichee (meme instant, meme mode que
  // le bandeau) : apres chaque chargement de la liste, la fiche est relue
  // avec les parametres de cette liste, ou reaffichee telle quelle (grisee)
  // si Bibler ne repond plus.
  function synchroniserPanneau() {
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
    return {
      radius: selection ? 10 : 7,
      fillColor: COULEURS[p.etat] || COULEURS.a_venir,
      fillOpacity: grise ? 0.4 : 0.9,
      color: consigne ? COULEUR_CONSIGNE : "#ffffff",
      weight: consigne ? 4 : 1.5,
      opacity: grise ? 0.4 : 1,
      dashArray: prevu ? "3 3" : null
    };
  }

  function infobulle(p) {
    var d = el("div", "bibler-infobulle");
    d.appendChild(el("strong", null, "Poste " + p.numero));
    if (p.affectation) d.appendChild(el("div", null, p.affectation));
    var prevu = donnees && donnees.mode === "prevu";
    d.appendChild(el("div", null, (LIBELLES_ETAT[p.etat] || p.etat || "") + " - " +
      (p.presents || 0) + "/" + (p.planifies || 0) + (prevu ? " prevu(s)" : " present(s)")));
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
        donnees ? "Aucun poste" : "Postes non charges"));
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
    if (panneau) { viderPanneau(); panneau.hidden = true; }
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
    // Reaffichage du meme poste : sections ouvertes et defilement gardes
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
        (resume.presents || 0) + "/" + (resume.planifies || 0) + (prevu ? " prevu(s)" : " present(s)")));
      if (prevu) ligne.appendChild(el("span", "bibler-tag-prevu", "Prevu a " + heureEtJour(donnees.instant)));
      else if (donnees && !donnees.direct) ligne.appendChild(el("span", "bibler-tag-reel", "a " + heureEtJour(donnees.instant)));
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
        pp.appendChild(el("span", "bibler-libelle", "Conduite particuliere : "));
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
      dmat.appendChild(el("summary", null, "Materiel (" + f.materiel.length + ")"));
      var ulm = el("ul");
      f.materiel.forEach(function (m) { ulm.appendChild(el("li", null, m)); });
      dmat.appendChild(ulm);
      corps.appendChild(dmat);
    }

    // Specimens controles
    if (f.specimens && f.specimens.length) {
      var ss = section("Specimens controles (" + f.specimens.length + ")");
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
    var sa = section((prevu ? "Agents prevus" : "Agents en service") + " (" + (f.agents || []).length + ")");
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
      var li = el("li", "bibler-consigne bibler-priorite-" + sansAccents(c.priorite || "").replace(/[^a-z_]/g, ""));
      li.dataset.consigne = c.id || "";
      var t = el("div", "bibler-consigne-titre", c.titre || "");
      if (c.priorite_libelle) t.appendChild(el("span", "bibler-consigne-priorite", c.priorite_libelle));
      li.appendChild(t);
      if (c.texte) li.appendChild(el("div", "bibler-consigne-texte", c.texte));
      var meta = [c.cible_libelle, c.auteur, c.heure].filter(Boolean).join(" - ");
      if (meta) li.appendChild(el("div", "bibler-consigne-meta", meta));
      listeC.appendChild(li);
    });
    sc.appendChild(listeC);
    corps.appendChild(sc);

    // Emplacement du formulaire "Donner une consigne" (Task 9, si window.BIBLER_PUBLISH)
    var actions = el("div", "bibler-panneau-actions");
    actions.id = "bibler-panneau-actions";
    actions.dataset.numero = String(f.numero);
    corps.appendChild(actions);

    panneau.appendChild(corps);
    panneau._fiche = f;
    panneau._params = params || null;
    panneau.hidden = false;
    Array.prototype.forEach.call(panneau.querySelectorAll("details > summary"), function (s) {
      if (ouverts[s.textContent]) s.parentNode.open = true;
    });
    if (defilement) corps.scrollTop = defilement;
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
    fermerSuggestions();
    fermerPanneau();
  }

  // Task 9 : surlignage des postes cibles d'une consigne et suivi.
  function surlignerConsigne(id) {
    if (!actif) activer();
    return id;
  }

  function initialiser() {
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
    surlignerConsigne: surlignerConsigne
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

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", attendreCarte);
  else attendreCarte();
})();
