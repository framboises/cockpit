// ==========================================================================
// METEO AVANCEE — radar sur la carte, contrainte thermique, vigilance
//
// Trois apports, greffes sur l'existant sans le remplacer :
//   1. une couche radar animee sur la carte (observation puis prevision)
//   2. des sections dans le bloc "Analyse Meteo" : WBGT, vigilance, sols
//   3. une pastille de vigilance dans le bandeau du header
//
// PRINCIPE : aucun calcul meteorologique ici. Les indices de contrainte
// thermique viennent de meteo_thermique.py cote serveur -- une formule qui
// engage une decision d'exploitation doit vivre a un seul endroit, testable,
// et pas etre dupliquee dans du JavaScript.
// ==========================================================================
(function () {
  "use strict";

  var API = {
    analyse: "/api/meteo/analyse",
    vigilance: "/api/meteo/vigilance",
    sequence: "/api/meteo/radar/sequence?heures=3",
    etat: "/api/meteo/etat"
  };

  // Couleurs de vigilance Meteo-France. Restituees telles quelles : ce sont
  // celles du producteur, on ne les reinterprete pas.
  var VIGILANCE = {
    vert:   { fond: "rgba(0,131,0,0.14)",   trait: "#008300", texte: "Pas de vigilance particuliere" },
    jaune:  { fond: "rgba(245,200,60,0.16)", trait: "#e0b400", texte: "Soyez attentif" },
    orange: { fond: "rgba(245,150,40,0.18)", trait: "#f59628", texte: "Soyez tres vigilant" },
    rouge:  { fond: "rgba(220,50,50,0.20)",  trait: "#dc3232", texte: "Vigilance absolue" }
  };

  var NIVEAUX = {
    normal:          { trait: "#3987e5", libelle: "Normal" },
    vigilance:       { trait: "#e0b400", libelle: "Vigilance" },
    vigilance_haute: { trait: "#f59628", libelle: "Vigilance haute" },
    danger:          { trait: "#dc3232", libelle: "Danger" },
    danger_extreme:  { trait: "#8b1a5a", libelle: "Danger extreme" }
  };

  var etat = { analyse: null, vigilance: null, sequence: null };

  function get(url) {
    return fetch(url).then(function (r) {
      if (!r.ok) throw new Error(url + " -> " + r.status);
      return r.json();
    });
  }

  function el(tag, cls, texte) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (texte !== undefined && texte !== null) n.textContent = String(texte);
    return n;
  }

  function heureCourte(iso) {
    if (!iso) return "";
    var d = new Date(iso);
    return isNaN(d) ? String(iso).slice(11, 16)
                    : String(d.getHours()).padStart(2, "0") + ":" +
                      String(d.getMinutes()).padStart(2, "0");
  }

  // ========================================================================
  // 1. RADAR SUR LA CARTE
  // ========================================================================

  var radar = {
    couche: null, images: [], index: 0, timer: null, bornes: null, actif: false
  };

  function carte() {
    return (window.CockpitMapView && window.CockpitMapView.getMap)
      ? window.CockpitMapView.getMap() : null;
  }

  function chargerSequence() {
    return get(API.sequence).then(function (d) {
      etat.sequence = d;
      radar.images = d.images || [];
      if (d.bbox) {
        radar.bornes = [[d.bbox.south, d.bbox.west], [d.bbox.north, d.bbox.east]];
      }
      return d;
    });
  }

  function afficherImage(i) {
    var m = carte();
    if (!m || !radar.images.length || !radar.bornes) return;
    i = ((i % radar.images.length) + radar.images.length) % radar.images.length;
    radar.index = i;
    var image = radar.images[i];

    if (!radar.couche) {
      radar.couche = L.imageOverlay(image.url, radar.bornes, {
        opacity: 0.72, interactive: false, zIndex: 450
      }).addTo(m);
    } else {
      radar.couche.setUrl(image.url);
    }
    majBarreRadar(image);
  }

  function majBarreRadar(image) {
    var barre = document.getElementById("radar-barre");
    if (!barre || !image) return;
    var prevision = image.flux === "prevision";
    barre.querySelector(".radar-heure").textContent = heureCourte(image.valid_at);
    var badge = barre.querySelector(".radar-type");
    badge.textContent = prevision
      ? ("prevision +" + (image.echeance_min || "?") + " min")
      : "observation";
    badge.style.color = prevision ? "#f59628" : "#3987e5";
    var curseur = barre.querySelector(".radar-curseur");
    if (curseur) curseur.value = radar.index;
    var maxi = barre.querySelector(".radar-max");
    if (maxi) {
      maxi.textContent = (image.max_mmh > 0)
        ? (image.max_mmh.toFixed(1) + " mm/h") : "pas de precipitation";
    }
  }

  function jouer() {
    arreter();
    radar.timer = setInterval(function () { afficherImage(radar.index + 1); }, 550);
    var b = document.getElementById("radar-play");
    if (b) b.querySelector("span").textContent = "pause";
  }

  function arreter() {
    if (radar.timer) { clearInterval(radar.timer); radar.timer = null; }
    var b = document.getElementById("radar-play");
    if (b) b.querySelector("span").textContent = "play_arrow";
  }

  function basculerRadar() {
    var m = carte();
    if (!m) { return; }
    if (radar.actif) {
      arreter();
      if (radar.couche) { m.removeLayer(radar.couche); radar.couche = null; }
      radar.actif = false;
      majBoutonRadar();
      var barre = document.getElementById("radar-barre");
      if (barre) barre.style.display = "none";
      return;
    }
    chargerSequence().then(function () {
      if (!radar.images.length) {
        if (window.showToast) window.showToast("Aucune image radar disponible", "error");
        return;
      }
      radar.actif = true;
      construireBarre();
      // On demarre sur la derniere observation, pas sur la premiere image :
      // c'est l'instant present qui interesse, le passe se rejoue ensuite.
      var derniereObs = 0;
      radar.images.forEach(function (im, i) { if (im.flux === "observation") derniereObs = i; });
      afficherImage(derniereObs);
      majBoutonRadar();
    }).catch(function (e) {
      console.error("[Meteo] sequence radar:", e);
      if (window.showToast) window.showToast("Radar indisponible", "error");
    });
  }

  function majBoutonRadar() {
    var b = document.getElementById("radar-toggle");
    if (!b) return;
    // "active" et non "actif" : c'est la classe que style.css applique deja
    // aux autres bascules de la carte (.cockpit-tile-switcher .tile-btn.active).
    b.classList.toggle("active", radar.actif);
    b.title = radar.actif ? "Masquer le radar de precipitations"
                          : "Radar de precipitations";
  }

  function construireBarre() {
    var existante = document.getElementById("radar-barre");
    if (existante) { existante.style.display = "flex"; return; }

    var conteneur = document.getElementById("cockpit-map");
    if (!conteneur) return;

    var barre = el("div", "radar-barre");
    barre.id = "radar-barre";

    var play = el("button", "radar-btn");
    play.id = "radar-play";
    play.appendChild(el("span", "material-symbols-outlined", "play_arrow"));
    play.addEventListener("click", function () {
      if (radar.timer) arreter(); else jouer();
    });

    var curseur = document.createElement("input");
    curseur.type = "range";
    curseur.className = "radar-curseur";
    curseur.min = 0;
    curseur.max = Math.max(0, radar.images.length - 1);
    curseur.value = 0;
    curseur.addEventListener("input", function () {
      arreter();
      afficherImage(parseInt(curseur.value, 10));
    });

    var infos = el("div", "radar-infos");
    infos.appendChild(el("span", "radar-heure", "--:--"));
    infos.appendChild(el("span", "radar-type", ""));
    infos.appendChild(el("span", "radar-max", ""));

    var source = el("span", "radar-source", "Source : Meteo-France");

    barre.appendChild(play);
    barre.appendChild(curseur);
    barre.appendChild(infos);
    barre.appendChild(source);
    conteneur.appendChild(barre);

    // Leaflet capte les evenements souris : sans cela, deplacer le curseur
    // ferait glisser la carte.
    if (window.L && L.DomEvent) {
      L.DomEvent.disableClickPropagation(barre);
      L.DomEvent.disableScrollPropagation(barre);
    }
  }

  function poserBoutonRadar() {
    var m = carte();
    if (!m || document.getElementById("radar-toggle")) return;

    // Memes classes que les autres bascules de la carte (map_view.js:234) :
    // cockpit-tile-switcher + tile-btn, dont l'etat .active est deja style
    // dans style.css. Rien a redupliquer, et le bouton suit automatiquement
    // toute evolution du theme.
    var controle = L.control({ position: "topright" });
    controle.onAdd = function () {
      var div = L.DomUtil.create("div", "leaflet-bar cockpit-tile-switcher cockpit-radar-ctrl");
      var bouton = document.createElement("button");
      bouton.className = "tile-btn";
      bouton.id = "radar-toggle";
      bouton.title = "Radar de precipitations";
      var icone = el("span", "material-symbols-outlined", "radar");
      icone.style.fontSize = "20px";
      bouton.appendChild(icone);
      div.appendChild(bouton);
      L.DomEvent.disableClickPropagation(div);
      bouton.addEventListener("click", basculerRadar);
      return div;
    };
    controle.addTo(m);
  }

  // ========================================================================
  // 2. SECTIONS DU BLOC ANALYSE METEO
  // ========================================================================

  function sectionVigilance(v) {
    var s = el("section", "meteo-section");
    var titre = el("h4", null, "Vigilance Meteo-France — Sarthe");
    s.appendChild(titre);

    if (!v || !v.disponible) {
      s.appendChild(el("p", "meteo-vide", "Aucun bulletin collecte."));
      return s;
    }

    var couleurMax = "vert";
    (v.periodes || []).forEach(function (p) {
      var ordre = ["vert", "jaune", "orange", "rouge"];
      if (ordre.indexOf(p.couleur_max) > ordre.indexOf(couleurMax)) couleurMax = p.couleur_max;
    });
    var style = VIGILANCE[couleurMax] || VIGILANCE.vert;

    var carte_ = el("div", "vigilance-carte");
    carte_.style.background = style.fond;
    carte_.style.borderLeft = "4px solid " + style.trait;

    var entete = el("div", "vigilance-entete");
    var pastille = el("span", "vigilance-pastille", couleurMax.toUpperCase());
    pastille.style.background = style.trait;
    entete.appendChild(pastille);
    entete.appendChild(el("span", "vigilance-libelle", style.texte));
    carte_.appendChild(entete);

    (v.periodes || []).forEach(function (p) {
      var ligne = el("div", "vigilance-periode");
      var quand = p.echeance === "J" ? "Aujourd'hui" : "Demain";
      ligne.appendChild(el("strong", null, quand));
      if (!p.phenomenes.length) {
        ligne.appendChild(el("span", "vigilance-phen", "aucun phenomene signale"));
      } else {
        p.phenomenes.forEach(function (ph) {
          var tag = el("span", "vigilance-phen", ph.nom + " — " + ph.couleur);
          tag.style.color = (VIGILANCE[ph.couleur] || {}).trait || "inherit";
          tag.style.fontWeight = "600";
          ligne.appendChild(tag);
        });
      }
      carte_.appendChild(ligne);
    });

    // Regle non negociable : l'horodatage accompagne toujours la valeur, et
    // un bulletin perime se declare tel plutot que de passer pour courant.
    var pied = el("div", "vigilance-pied");
    pied.textContent = "Bulletin du " + (v.update_time || "?") +
      (v.age_h !== null && v.age_h !== undefined ? "  (il y a " + v.age_h + " h)" : "");
    if (v.perime) {
      pied.textContent += "  —  PERIME (> " + v.peremption_h + " h), a ne pas utiliser tel quel";
      pied.style.color = "#dc3232";
      pied.style.fontWeight = "700";
    }
    carte_.appendChild(pied);
    s.appendChild(carte_);
    return s;
  }

  function sectionThermique(analyse) {
    var s = el("section", "meteo-section");
    s.appendChild(el("h4", null, "Contrainte thermique — WBGT"));

    s.appendChild(el("p", "meteo-note",
      "WBGT approche a partir de la temperature et de l'humidite (methode du " +
      "Bureau of Meteorology australien), temperature humide selon Stull (2011). " +
      "Le rayonnement et le vent n'entrent pas dans ce calcul : l'indice est " +
      "signale comme surestime la nuit, par vent soutenu ou par forte humidite."));

    var grille = el("div", "wbgt-jours");
    (analyse.jours || []).forEach(function (jour) {
      var ct = jour.contrainte;
      if (!ct || !ct.pic) return;
      var niveau = NIVEAUX[ct.pic.wbgt_niveau] || NIVEAUX.normal;

      var carte_ = el("div", "wbgt-jour");
      carte_.style.borderTop = "3px solid " + niveau.trait;
      carte_.appendChild(el("div", "wbgt-date", jour.date));
      var val = el("div", "wbgt-valeur", ct.pic.wbgt_c + " °C");
      val.style.color = niveau.trait;
      carte_.appendChild(val);
      carte_.appendChild(el("div", "wbgt-niveau", niveau.libelle));
      carte_.appendChild(el("div", "wbgt-detail", "pic a " + ct.pic.heure));

      if (ct.heures_au_dessus > 0) {
        carte_.appendChild(el("div", "wbgt-plage",
          ct.heures_au_dessus + " h >= " + ct.seuil_vigilance + " °C  (" +
          ct.debut + " - " + ct.fin + ")"));
      }
      if (ct.pic.wbgt_consigne) {
        carte_.appendChild(el("div", "wbgt-consigne", ct.pic.wbgt_consigne));
      }
      if (ct.pic.wbgt_fiabilite && ct.pic.wbgt_fiabilite !== "bonne") {
        var res = el("div", "wbgt-reserve", "! " + ct.pic.wbgt_fiabilite);
        res.title = (ct.pic.wbgt_reserves || []).join(" ; ");
        carte_.appendChild(res);
      }
      grille.appendChild(carte_);
    });

    if (!grille.children.length) {
      s.appendChild(el("p", "meteo-vide",
        "Indice non calculable : l'humidite manque sur ces echeances."));
    } else {
      s.appendChild(grille);
    }
    return s;
  }

  function sectionSol(analyse) {
    var s = el("section", "meteo-section");
    s.appendChild(el("h4", null, "Humidite des sols — risque incendie et portance"));
    var sol = analyse.sol;
    if (!sol) {
      s.appendChild(el("p", "meteo-vide", "Aucune donnee SWI collectee."));
      return s;
    }

    s.appendChild(el("p", "meteo-note",
      "SWI : reserve en eau du sol, de 0 (point de fletrissement) a 1 et au-dela " +
      "(sol sature). SSWI sur 10 jours : anomalie par rapport a la normale. " +
      "Maille SAFRAN 8 km du circuit. C'est l'indicateur de secheresse installee, " +
      "la ou un cumul de pluie ne dit rien de la reserve du sol."));

    var ligne = el("div", "sol-ligne");
    function bloc(libelle, valeur, couleur, note) {
      var b = el("div", "sol-bloc");
      b.style.borderLeft = "3px solid " + couleur;
      b.appendChild(el("div", "sol-libelle", libelle));
      var v = el("div", "sol-valeur", valeur);
      v.style.color = couleur;
      b.appendChild(v);
      if (note) b.appendChild(el("div", "sol-note", note));
      return b;
    }

    var swi = sol.swi;
    var couleurSwi = swi < 0.2 ? "#dc3232" : (swi < 0.4 ? "#f59628" : "#008300");
    var etatSwi = swi < 0.2 ? "sols tres secs" : (swi < 0.4 ? "sols secs" : "reserve correcte");
    ligne.appendChild(bloc("SWI", (swi !== null ? swi.toFixed(3) : "--"), couleurSwi, etatSwi));

    var anomalie = sol.sswi_10j;
    var couleurAno = anomalie < -1 ? "#dc3232" : (anomalie < -0.5 ? "#f59628" : "#008300");
    ligne.appendChild(bloc("SSWI 10 j",
      (anomalie !== null ? (anomalie > 0 ? "+" : "") + anomalie.toFixed(2) : "--"),
      couleurAno, anomalie < -1 ? "anomalie marquee" : "dans la normale"));

    ligne.appendChild(bloc("ETP", (sol.etp !== null ? sol.etp + " mm" : "--"),
      "#3987e5", "evapotranspiration"));
    ligne.appendChild(bloc("Pluie", (sol.preliq !== null ? sol.preliq + " mm" : "--"),
      "#3987e5", "sur la maille"));

    s.appendChild(ligne);

    // Tendance sur 30 jours, en barres : dit si l'assechement se poursuit.
    var serie = analyse.sol_serie || [];
    if (serie.length > 3) {
      var spark = el("div", "sol-spark");
      var vals = serie.map(function (x) { return x.swi; }).filter(function (x) { return x !== null; });
      var mini = Math.min.apply(null, vals), maxi = Math.max.apply(null, vals);
      var etendue = (maxi - mini) || 1;
      serie.forEach(function (x) {
        var barre = el("div", "sol-barre");
        var h = x.swi === null ? 0 : ((x.swi - mini) / etendue) * 100;
        barre.style.height = Math.max(4, h) + "%";
        barre.style.background = x.swi < 0.2 ? "#dc3232" : (x.swi < 0.4 ? "#f59628" : "#008300");
        barre.title = x.date + " : SWI " + (x.swi !== null ? x.swi.toFixed(3) : "--");
        spark.appendChild(barre);
      });
      s.appendChild(el("div", "sol-spark-titre", "SWI sur 30 jours"));
      s.appendChild(spark);
    }
    return s;
  }

  function sectionRadar(analyse) {
    var s = el("section", "meteo-section");
    s.appendChild(el("h4", null, "Radar de precipitations"));
    var r = analyse.radar;
    if (!r || !r.png) {
      s.appendChild(el("p", "meteo-vide", "Aucune image radar collectee."));
      return s;
    }
    s.appendChild(el("p", "meteo-note",
      "Lame d'eau observee, maille 500 m, actualisee toutes les 5 minutes. " +
      "L'API ne sert que la derniere image : l'animation du passe n'existe que " +
      "si la collecte n'a pas ete interrompue."));

    var ligne = el("div", "radar-resume");
    ligne.appendChild(el("span", null, "Derniere image : " + heureCourte(r.valid_at)));
    ligne.appendChild(el("span", null,
      r.max_mmh > 0 ? ("maximum " + r.max_mmh + " mm/h sur la zone")
                    : "aucune precipitation detectee"));
    var bouton = el("button", "meteo-bouton", "Afficher sur la carte");
    bouton.addEventListener("click", function () {
      var panneau = document.getElementById("meteo-panel-close");
      if (panneau) panneau.click();
      if (!radar.actif) basculerRadar();
    });
    ligne.appendChild(bouton);
    s.appendChild(ligne);
    return s;
  }

  // ========================================================================
  // MODE DASHBOARD : synthese + table horaire complete
  // ========================================================================

  function tuile(libelle, valeur, note, couleur) {
    var t = el("div", "dash-tuile");
    if (couleur) t.style.borderLeftColor = couleur;
    t.appendChild(el("div", "dash-tuile-libelle", libelle));
    var v = el("div", "dash-tuile-valeur", valeur);
    if (couleur) v.style.color = couleur;
    t.appendChild(v);
    if (note) t.appendChild(el("div", "dash-tuile-note", note));
    return t;
  }

  function synthese(analyse, vigilance) {
    var bloc = el("div", "dash-synthese");
    var jours = analyse.jours || [];
    var heures = [];
    jours.forEach(function (j) { heures = heures.concat(j.heures || []); });
    if (!heures.length) return bloc;

    function extremum(champ, comparateur) {
      var v = heures.map(function (h) { return h[champ]; })
                    .filter(function (x) { return x !== null && x !== undefined; });
      return v.length ? v.reduce(comparateur) : null;
    }
    var tmax = extremum("temperature_c", function (a, b) { return Math.max(a, b); });
    var tmin = extremum("temperature_c", function (a, b) { return Math.min(a, b); });
    var rafale = extremum("vent_rafale_kmh", function (a, b) { return Math.max(a, b); });
    var pluie = heures.reduce(function (s, h) { return s + (h.pluie_mm || 0); }, 0);
    var wbgt = extremum("wbgt_c", function (a, b) { return Math.max(a, b); });

    var vigCouleur = "vert";
    var ordre = ["vert", "jaune", "orange", "rouge"];
    if (vigilance && vigilance.periodes) {
      vigilance.periodes.forEach(function (p) {
        if (ordre.indexOf(p.couleur_max) > ordre.indexOf(vigCouleur)) vigCouleur = p.couleur_max;
      });
    }
    bloc.appendChild(tuile("Vigilance", vigCouleur.toUpperCase(),
      (vigilance && vigilance.perime) ? "bulletin perime" : "Meteo-France",
      (VIGILANCE[vigCouleur] || VIGILANCE.vert).trait));

    bloc.appendChild(tuile("Temperature",
      (tmax !== null ? tmax.toFixed(0) + " °C" : "--"),
      (tmin !== null ? "mini " + tmin.toFixed(0) + " °C" : ""), "#3987e5"));

    var niveauWbgt = NIVEAUX[(heures.filter(function (h) { return h.wbgt_c === wbgt; })[0] || {}).wbgt_niveau] || NIVEAUX.normal;
    bloc.appendChild(tuile("WBGT max",
      (wbgt !== null ? wbgt.toFixed(1) + " °C" : "--"),
      niveauWbgt.libelle, niveauWbgt.trait));

    bloc.appendChild(tuile("Rafale max",
      (rafale !== null ? rafale.toFixed(0) + " km/h" : "--"), "sur 4 jours",
      rafale >= 80 ? "#dc3232" : (rafale >= 60 ? "#f59628" : "#3987e5")));

    bloc.appendChild(tuile("Cumul pluie", pluie.toFixed(1) + " mm", "sur 4 jours",
      pluie >= 30 ? "#f59628" : "#3987e5"));

    // Orage : la foudre declenche une mise a l'abri, pas la pluie.
    var orages = heures.filter(function (h) {
      return h.orage && (h.orage.niveau === "avere" || h.orage.niveau === "possible");
    });
    bloc.appendChild(tuile("Orage",
      orages.length ? orages.length + " h" : "aucun",
      orages.length ? "foudre prevue" : "sur 4 jours",
      orages.length ? "#dc3232" : "#008300"));

    if (analyse.sol && analyse.sol.swi !== null && analyse.sol.swi !== undefined) {
      var swi = analyse.sol.swi;
      bloc.appendChild(tuile("SWI sols", swi.toFixed(3),
        swi < 0.2 ? "tres secs" : (swi < 0.4 ? "secs" : "reserve correcte"),
        swi < 0.2 ? "#dc3232" : (swi < 0.4 ? "#f59628" : "#008300")));
    }
    return bloc;
  }

  var COLONNES = [
    ["heure", "Heure", function (h) { return h.heure; }],
    ["temperature_c", "T °C", function (h) { return fmt(h.temperature_c, 0); }],
    ["tw_c", "Th °C", function (h) { return fmt(h.tw_c, 1); }],
    ["wbgt_c", "WBGT", function (h) { return fmt(h.wbgt_c, 1); }],
    ["humidite_pct", "HR %", function (h) { return fmt(h.humidite_pct, 0); }],
    ["pluie_mm", "Pluie", function (h) { return fmt(h.pluie_mm, 1); }],
    ["vent_moyen_kmh", "Vent", function (h) { return fmt(h.vent_moyen_kmh, 0); }],
    ["vent_rafale_kmh", "Rafale", function (h) { return fmt(h.vent_rafale_kmh, 0); }],
    ["vent_direction", "Dir", function (h) { return h.vent_direction || "--"; }],
    ["nebulosite_pct", "Nuages", function (h) { return fmt(h.nebulosite_pct, 0); }],
    ["rayonnement_wm2", "Soleil", function (h) { return fmt(h.rayonnement_wm2, 0); }],
    ["cape", "CAPE", function (h) { return fmt(h.cape, 0); }],
    ["foudre", "Foudre", function (h) { return fmt(h.foudre, 1); }],
    ["visibilite_m", "Visib km", function (h) {
      return h.visibilite_m == null ? "--" : (h.visibilite_m / 1000).toFixed(1); }],
    ["pression_hpa", "Pression", function (h) { return fmt(h.pression_hpa, 0); }],
    ["source", "Modele", function (h) { return h.source || "--"; }]
  ];

  function fmt(v, d) { return (v === null || v === undefined) ? "--" : Number(v).toFixed(d); }

  // Seuils d'alerte par colonne. La couleur ne porte jamais l'information
  // seule : la valeur reste lisible, la couleur ne fait que la hierarchiser.
  function classeSeuil(cle, h) {
    var v = h[cle];
    if (v === null || v === undefined) return "";
    if (cle === "vent_rafale_kmh") return v >= 80 ? "seuil-critique" : (v >= 60 ? "seuil-danger" : (v >= 40 ? "seuil-vigilance" : ""));
    if (cle === "pluie_mm") return v >= 15 ? "seuil-critique" : (v >= 5 ? "seuil-danger" : (v >= 1 ? "seuil-vigilance" : ""));
    if (cle === "wbgt_c") return v >= 30 ? "seuil-critique" : (v >= 28 ? "seuil-danger" : (v >= 25 ? "seuil-vigilance" : ""));
    if (cle === "temperature_c") return v >= 35 ? "seuil-critique" : (v >= 30 ? "seuil-danger" : (v <= 2 ? "seuil-vigilance" : ""));
    if (cle === "cape") return v >= 2500 ? "seuil-critique" : (v >= 1500 ? "seuil-danger" : (v >= 800 ? "seuil-vigilance" : ""));
    if (cle === "foudre") return v > 0.5 ? "seuil-critique" : (v > 0 ? "seuil-danger" : "");
    if (cle === "visibilite_m") return v < 1000 ? "seuil-critique" : (v < 5000 ? "seuil-danger" : "");
    return "";
  }

  function tableHoraire(jour) {
    var enveloppe = el("div", "dash-table-wrap");
    var table = el("table", "dash-heures");

    var thead = document.createElement("thead");
    var trh = document.createElement("tr");
    COLONNES.forEach(function (c) { trh.appendChild(el("th", null, c[1])); });
    thead.appendChild(trh);
    table.appendChild(thead);

    var tbody = document.createElement("tbody");
    (jour.heures || []).forEach(function (h) {
      var tr = document.createElement("tr");
      var heureNum = parseInt(String(h.heure || "0").split(":")[0], 10);
      // Les heures nocturnes sont attenuees : elles restent lisibles mais ne
      // captent pas l'oeil, l'exploitation se joue de jour.
      if (heureNum < 7 || heureNum >= 22) tr.className = "nuit";
      COLONNES.forEach(function (c) {
        var td = el("td", classeSeuil(c[0], h), c[2](h));
        if (c[0] === "vent_direction" && h.vent_direction_deg != null) {
          td.textContent = "";
          var fleche = el("span", "dash-fleche", "↓");
          // La fleche pointe vers OU VA le vent : la direction meteo dit d'ou
          // il vient, on ajoute donc 180 degres pour l'affichage.
          fleche.style.transform = "rotate(" + ((h.vent_direction_deg + 180) % 360) + "deg)";
          fleche.title = "vient du " + h.vent_direction + " (" + h.vent_direction_deg + "°)";
          td.appendChild(fleche);
          td.appendChild(document.createTextNode(" " + h.vent_direction));
        }
        if (c[0] === "wbgt_c" && h.wbgt_fiabilite && h.wbgt_fiabilite !== "bonne") {
          td.textContent += " !";
          td.title = (h.wbgt_reserves || []).join(" ; ");
        }
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    enveloppe.appendChild(table);
    return enveloppe;
  }

  function sectionDashboard(analyse, vigilance) {
    var s = el("section", "meteo-section");
    s.id = "meteo-sec-dashboard";
    s.appendChild(el("h4", null, "Tableau de bord — 4 jours heure par heure"));
    s.appendChild(synthese(analyse, vigilance));

    s.appendChild(el("p", "meteo-note",
      "Th : temperature humide. WBGT : contrainte thermique (! = valeur a relire, " +
      "survolez). Soleil : rayonnement en W/m2. CAPE : energie disponible pour " +
      "la convection. La fleche de vent pointe vers ou il souffle. " +
      "Source : Meteo-France."));

    (analyse.jours || []).forEach(function (jour) {
      var titre = el("div", "dash-jour-titre", jour.date);
      var sources = {};
      (jour.heures || []).forEach(function (h) { if (h.source) sources[h.source] = 1; });
      titre.appendChild(el("span", "dash-source", Object.keys(sources).join(" + ")));
      s.appendChild(titre);
      s.appendChild(tableHoraire(jour));
    });
    return s;
  }

  function basculerDashboard() {
    var panneau = document.getElementById("meteo-panel");
    if (!panneau) return;
    var actif = panneau.classList.toggle("dashboard");
    var bouton = document.getElementById("meteo-dash-toggle");
    if (bouton) {
      bouton.querySelector("span").textContent = actif ? "close_fullscreen" : "table_rows";
      bouton.title = actif ? "Vue compacte" : "Tableau de bord complet";
    }
    var section = document.getElementById("meteo-sec-dashboard");
    if (actif && !section && etat.analyse) {
      var corps = document.getElementById("meteo-panel-body");
      corps.insertBefore(sectionDashboard(etat.analyse, etat.vigilance), corps.firstChild);
    } else if (section) {
      section.style.display = actif ? "" : "none";
    }
  }

  function poserBoutonDashboard() {
    var entete = document.querySelector(".meteo-panel-header");
    if (!entete || document.getElementById("meteo-dash-toggle")) return;
    var bouton = el("button", "meteo-dash-toggle");
    bouton.id = "meteo-dash-toggle";
    bouton.title = "Tableau de bord complet";
    bouton.appendChild(el("span", "material-symbols-outlined", "table_rows"));
    bouton.addEventListener("click", basculerDashboard);
    var fermer = document.getElementById("meteo-panel-close");
    if (fermer) entete.insertBefore(bouton, fermer);
    else entete.appendChild(bouton);
  }

  function injecterSections() {
    var corps = document.getElementById("meteo-panel-body");
    if (!corps || !etat.analyse) return;

    ["meteo-sec-vigilance", "meteo-sec-thermique", "meteo-sec-sol", "meteo-sec-radar"]
      .forEach(function (id) {
        var n = document.getElementById(id);
        if (n) n.remove();
      });

    var sections = [
      ["meteo-sec-vigilance", sectionVigilance(etat.vigilance)],
      ["meteo-sec-thermique", sectionThermique(etat.analyse)],
      ["meteo-sec-radar", sectionRadar(etat.analyse)],
      ["meteo-sec-sol", sectionSol(etat.analyse)]
    ];
    // En tete du panneau : la vigilance et la contrainte thermique passent
    // avant les graphiques, elles portent la decision.
    var premier = corps.firstChild;
    sections.forEach(function (paire) {
      paire[1].id = paire[0];
      corps.insertBefore(paire[1], premier);
    });
  }

  // ========================================================================
  // 3. PASTILLE DE VIGILANCE DANS LE BANDEAU
  // ========================================================================

  function pastilleBandeau(v) {
    var hote = document.getElementById("meteo-previsions");
    if (!hote || !v || !v.disponible) return;

    var couleurMax = "vert";
    var ordre = ["vert", "jaune", "orange", "rouge"];
    (v.periodes || []).forEach(function (p) {
      if (ordre.indexOf(p.couleur_max) > ordre.indexOf(couleurMax)) couleurMax = p.couleur_max;
    });
    // La jauge du widget Meteo porte desormais la vigilance (elle prime sur
    // les seuils internes, cf. app.py). Cette pastille ne se justifie donc
    // plus que pour le cas que la jauge ne peut pas montrer : un bulletin
    // perime, qui n'eleve plus le niveau mais doit rester visible.
    if (!v.perime) return;

    var ancienne = document.getElementById("vigilance-pastille-bandeau");
    if (ancienne) ancienne.remove();

    var style = VIGILANCE[couleurMax] || VIGILANCE.vert;
    var p = el("div", "vigilance-bandeau");
    p.id = "vigilance-pastille-bandeau";
    p.style.background = style.trait;
    var phen = [];
    (v.periodes || []).forEach(function (per) {
      (per.phenomenes || []).forEach(function (x) {
        if (phen.indexOf(x.nom) === -1) phen.push(x.nom);
      });
    });
    p.textContent = v.perime ? "VIGILANCE PERIMEE"
                             : ("VIGILANCE " + couleurMax.toUpperCase() +
                                (phen.length ? " — " + phen.join(", ") : ""));
    p.title = "Bulletin Meteo-France du " + (v.update_time || "?");
    hote.insertBefore(p, hote.firstChild);
  }

  // ========================================================================
  // Amorce
  // ========================================================================

  function charger() {
    return Promise.all([
      get(API.analyse).catch(function () { return null; }),
      get(API.vigilance).catch(function () { return null; })
    ]).then(function (res) {
      etat.analyse = res[0];
      etat.vigilance = res[1];
      if (etat.analyse) injecterSections();
      if (etat.vigilance) pastilleBandeau(etat.vigilance);
    });
  }

  function amorcer() {
    if (!document.getElementById("meteo-panel-body")) return;
    charger();
    poserBoutonDashboard();
    // La carte peut n'etre prete qu'apres ce script : on attend qu'elle existe
    // plutot que de supposer un ordre de chargement.
    var essais = 0;
    var attente = setInterval(function () {
      if (carte()) { clearInterval(attente); poserBoutonRadar(); }
      else if (++essais > 40) { clearInterval(attente); }
    }, 300);
    setInterval(charger, 10 * 60 * 1000);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", amorcer);
  } else {
    amorcer();
  }

  window.MeteoAvancee = {
    recharger: charger,
    basculerRadar: basculerRadar,
    basculerDashboard: basculerDashboard,
    etat: function () { return etat; }
  };
})();

// ==========================================================================
// MODALE METEO DU JOUR — remplacement de openMeteoModal
//
// La modale d'origine (meteo.js) affichait un tableau historique et une
// courbe temperature/pluie. Elle est ici remplacee par un tableau de bord
// complet, sans toucher a meteo.js : on surcharge la fonction globale.
//
// L'historique sur 5 ans est CONSERVE -- c'est la seule vue qui replace la
// journee dans son contexte pluriannuel, et elle n'existe nulle part ailleurs.
// ==========================================================================
(function () {
  "use strict";

  var originale = window.openMeteoModal;
  if (typeof originale !== "function") return;

  function E(tag, cls, txt) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (txt !== undefined && txt !== null) n.textContent = String(txt);
    return n;
  }
  function F(v, d) { return (v === null || v === undefined) ? "--" : Number(v).toFixed(d); }
  function ou(v) { return (v === null || v === undefined) ? "--" : v; }

  var NIV = {
    normal: "#3987e5", vigilance: "#e0b400", vigilance_haute: "#f59628",
    danger: "#dc3232", danger_extreme: "#8b1a5a"
  };

  function tuileM(libelle, valeur, note, couleur) {
    var t = E("div", "dash-tuile");
    if (couleur) t.style.borderLeftColor = couleur;
    t.appendChild(E("div", "dash-tuile-libelle", libelle));
    var v = E("div", "dash-tuile-valeur", valeur);
    if (couleur) v.style.color = couleur;
    t.appendChild(v);
    if (note) t.appendChild(E("div", "dash-tuile-note", note));
    return t;
  }

  function syntheseJour(jour) {
    var bloc = E("div", "dash-synthese");
    var h = jour.heures || [];
    if (!h.length) return bloc;

    function ext(champ, f) {
      var v = h.map(function (x) { return x[champ]; })
               .filter(function (x) { return x !== null && x !== undefined; });
      return v.length ? v.reduce(f) : null;
    }
    var max = function (a, b) { return Math.max(a, b); };
    var min = function (a, b) { return Math.min(a, b); };

    var tmax = ext("temperature_c", max), tmin = ext("temperature_c", min);
    bloc.appendChild(tuileM("Temperature", F(tmax, 0) + " °C",
      "mini " + F(tmin, 0) + " °C", "#3987e5"));

    var ct = jour.contrainte;
    if (ct && ct.pic) {
      var couleur = NIV[ct.pic.wbgt_niveau] || NIV.normal;
      bloc.appendChild(tuileM("WBGT max", F(ct.pic.wbgt_c, 1) + " °C",
        ct.heures_au_dessus > 0
          ? (ct.heures_au_dessus + " h >= " + ct.seuil_vigilance + " °C")
          : "sous le seuil", couleur));
    }

    var rafale = ext("vent_rafale_kmh", max);
    // Direction dominante : celle de l'heure la plus ventee, la seule qui
    // compte pour savoir quelle structure est exposee.
    var plusVentee = h.filter(function (x) { return x.vent_rafale_kmh === rafale; })[0] || {};
    var tuileVent = tuileM("Rafale max", F(rafale, 0) + " km/h",
      plusVentee.vent_direction ? ("de " + plusVentee.vent_direction) : "",
      rafale >= 80 ? "#dc3232" : (rafale >= 60 ? "#f59628" : "#3987e5"));
    if (plusVentee.vent_direction_deg != null) {
      var rose = E("span", "mj-rose", "↓");
      rose.style.transform = "rotate(" + ((plusVentee.vent_direction_deg + 180) % 360) + "deg)";
      rose.title = "souffle vers le " + plusVentee.vent_direction_deg + "°";
      tuileVent.appendChild(rose);
    }
    bloc.appendChild(tuileVent);

    var pluie = h.reduce(function (s, x) { return s + (x.pluie_mm || 0); }, 0);
    var heuresPluie = h.filter(function (x) { return (x.pluie_mm || 0) > 0.1; }).length;
    bloc.appendChild(tuileM("Cumul pluie", pluie.toFixed(1) + " mm",
      heuresPluie ? (heuresPluie + " h de pluie") : "journee seche",
      pluie >= 15 ? "#dc3232" : (pluie >= 5 ? "#f59628" : "#3987e5")));

    var orages = h.filter(function (x) {
      return x.orage && (x.orage.niveau === "avere" || x.orage.niveau === "possible");
    });
    bloc.appendChild(tuileM("Orage", orages.length ? orages.length + " h" : "aucun",
      orages.length ? (orages[0].heure + " -> " + orages[orages.length - 1].heure)
                    : "foudre non prevue",
      orages.length ? "#dc3232" : "#008300"));

    var ray = ext("rayonnement_wm2", max);
    var neb = h.map(function (x) { return x.nebulosite_pct; })
               .filter(function (x) { return x != null; });
    var nebMoy = neb.length ? neb.reduce(function (a, b) { return a + b; }, 0) / neb.length : null;
    bloc.appendChild(tuileM("Ensoleillement", F(ray, 0) + " W/m2",
      nebMoy !== null ? ("nuages " + F(nebMoy, 0) + " %") : "", "#e0b400"));

    return bloc;
  }

  var COLS_M = [
    ["heure", "Heure", function (h) { return h.heure; }],
    ["temperature_c", "T °C", function (h) { return F(h.temperature_c, 1); }],
    ["tw_c", "Th °C", function (h) { return F(h.tw_c, 1); }],
    ["wbgt_c", "WBGT", function (h) { return F(h.wbgt_c, 1); }],
    ["humidex", "Humidex", function (h) { return F(h.humidex, 1); }],
    ["humidite_pct", "HR %", function (h) { return F(h.humidite_pct, 0); }],
    ["pluie_mm", "Pluie mm", function (h) { return F(h.pluie_mm, 1); }],
    ["vent_moyen_kmh", "Vent", function (h) { return F(h.vent_moyen_kmh, 0); }],
    ["vent_rafale_kmh", "Rafale", function (h) { return F(h.vent_rafale_kmh, 0); }],
    ["vent_direction", "Dir", function (h) { return h.vent_direction || "--"; }],
    ["nebulosite_pct", "Nuages %", function (h) { return F(h.nebulosite_pct, 0); }],
    ["rayonnement_wm2", "W/m2", function (h) { return F(h.rayonnement_wm2, 0); }],
    ["cape", "CAPE", function (h) { return F(h.cape, 0); }],
    ["foudre", "Foudre", function (h) { return F(h.foudre, 2); }],
    ["visibilite_m", "Vis km", function (h) {
      return h.visibilite_m == null ? "--" : (h.visibilite_m / 1000).toFixed(1); }],
    ["pression_hpa", "hPa", function (h) { return F(h.pression_hpa, 0); }],
    ["revision_temp", "Revis.", function (h) {
      var r = h.revision_temp;
      return (r === null || r === undefined || r === 0) ? "" : (r > 0 ? "+" : "") + r; }],
    ["source", "Modele", function (h) { return h.source || "--"; }]
  ];

  function seuilM(cle, h) {
    var v = h[cle];
    if (v === null || v === undefined) return "";
    if (cle === "vent_rafale_kmh") return v >= 80 ? "seuil-critique" : (v >= 60 ? "seuil-danger" : (v >= 40 ? "seuil-vigilance" : ""));
    if (cle === "pluie_mm") return v >= 15 ? "seuil-critique" : (v >= 5 ? "seuil-danger" : (v >= 1 ? "seuil-vigilance" : ""));
    if (cle === "wbgt_c") return v >= 30 ? "seuil-critique" : (v >= 28 ? "seuil-danger" : (v >= 25 ? "seuil-vigilance" : ""));
    if (cle === "humidex") return v >= 45 ? "seuil-critique" : (v >= 40 ? "seuil-danger" : (v >= 30 ? "seuil-vigilance" : ""));
    if (cle === "temperature_c") return v >= 35 ? "seuil-critique" : (v >= 30 ? "seuil-danger" : (v <= 2 ? "seuil-vigilance" : ""));
    if (cle === "cape") return v >= 2500 ? "seuil-critique" : (v >= 1500 ? "seuil-danger" : (v >= 800 ? "seuil-vigilance" : ""));
    if (cle === "foudre") return v > 0.5 ? "seuil-critique" : (v > 0 ? "seuil-danger" : "");
    if (cle === "visibilite_m") return v < 1000 ? "seuil-critique" : (v < 5000 ? "seuil-danger" : "");
    return "";
  }

  function tableJour(jour) {
    var wrap = E("div", "mj-table-wrap");
    var table = E("table", "dash-heures");
    var thead = document.createElement("thead");
    var trh = document.createElement("tr");
    COLS_M.forEach(function (c) { trh.appendChild(E("th", null, c[1])); });
    thead.appendChild(trh); table.appendChild(thead);

    var tbody = document.createElement("tbody");
    (jour.heures || []).forEach(function (h) {
      var tr = document.createElement("tr");
      var hn = parseInt(String(h.heure || "0").split(":")[0], 10);
      if (hn < 7 || hn >= 22) tr.className = "nuit";
      COLS_M.forEach(function (c) {
        var td = E("td", seuilM(c[0], h), c[2](h));
        if (c[0] === "vent_direction" && h.vent_direction_deg != null) {
          td.textContent = "";
          var fl = E("span", "dash-fleche", "↓");
          fl.style.transform = "rotate(" + ((h.vent_direction_deg + 180) % 360) + "deg)";
          fl.title = "vient du " + h.vent_direction + " (" + h.vent_direction_deg + "°)";
          td.appendChild(fl);
          td.appendChild(document.createTextNode(" " + h.vent_direction));
        }
        if (c[0] === "wbgt_c" && h.wbgt_fiabilite && h.wbgt_fiabilite !== "bonne") {
          td.textContent += " !";
          td.title = (h.wbgt_reserves || []).join(" ; ");
        }
        if (c[0] === "revision_temp" && h.revision_temp) {
          td.title = "Le modele s'est ravise de " + h.revision_temp +
                     " °C depuis le passage precedent";
        }
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    wrap.appendChild(table);
    return wrap;
  }

  function construire(date, jour, historique) {
    var hote = document.getElementById("meteo-details");
    if (!hote) return;
    hote.textContent = "";

    var entete = E("div", "mj-entete");
    entete.appendChild(E("span", "mj-titre", "Meteo du " + date));
    var sources = {};
    (jour.heures || []).forEach(function (h) { if (h.source) sources[h.source] = 1; });
    entete.appendChild(E("span", "mj-sous",
      Object.keys(sources).join(" + ") + " — Source : Meteo-France"));
    var fermer = E("button", "mj-fermer", "×");
    fermer.addEventListener("click", function () {
      // Retirer l'elargissement : la modale #meteoModal est partagee, la
      // laisser en 1320 px deformerait le prochain contenu qui l'utilise.
      var m = document.getElementById("meteoModal");
      if (m) m.classList.remove("meteo-modal-large");
      if (window.closeMeteoModal) window.closeMeteoModal();
    });
    entete.appendChild(fermer);
    hote.appendChild(entete);

    var corps = E("div", "mj-corps");
    corps.appendChild(syntheseJour(jour));

    var onglets = E("div", "mj-onglets");
    var vues = {};
    [["heures", "Heure par heure"], ["courbes", "Courbes"], ["histo", "Historique 5 ans"]]
      .forEach(function (paire, i) {
        var b = E("button", "mj-onglet" + (i === 0 ? " actif" : ""), paire[1]);
        b.addEventListener("click", function () {
          onglets.querySelectorAll(".mj-onglet").forEach(function (x) { x.classList.remove("actif"); });
          b.classList.add("actif");
          Object.keys(vues).forEach(function (k) { vues[k].classList.remove("actif"); });
          vues[paire[0]].classList.add("actif");
          if (paire[0] === "courbes") dessiner(jour);
        });
        onglets.appendChild(b);
        var v = E("div", "mj-vue" + (i === 0 ? " actif" : ""));
        vues[paire[0]] = v;
      });
    corps.appendChild(onglets);

    vues.heures.appendChild(tableJour(jour));

    var cadre = E("div", "mj-graph");
    var canvas = document.createElement("canvas");
    canvas.id = "mj-canvas";
    cadre.appendChild(canvas);
    vues.courbes.appendChild(cadre);

    vues.histo.appendChild(tableHistorique(historique));

    Object.keys(vues).forEach(function (k) { corps.appendChild(vues[k]); });
    hote.appendChild(corps);
  }

  function tableHistorique(historique) {
    var wrap = E("div", "mj-table-wrap");
    if (!historique || !Object.keys(historique).length) {
      wrap.appendChild(E("p", "meteo-vide", "Aucun historique disponible."));
      return wrap;
    }
    var table = E("table", "dash-heures");
    var thead = document.createElement("thead");
    var trh = document.createElement("tr");
    ["Annee", "Precip. mois", "T min mois", "T max mois", "T moy mois", "T jour", "Pluie jour"]
      .forEach(function (t) { trh.appendChild(E("th", null, t)); });
    thead.appendChild(trh); table.appendChild(thead);

    var tbody = document.createElement("tbody");
    Object.keys(historique).sort().reverse().forEach(function (annee) {
      var d = historique[annee];
      var tr = document.createElement("tr");
      tr.appendChild(E("td", null, annee));
      // ou() plutot que ?? : le reste du fichier est en ES5, et une seule
      // syntaxe recente suffirait a casser la page sur un navigateur ancien.
      tr.appendChild(E("td", null, ou(d["Précipitations Totales Mois (mm)"]) + " mm"));
      tr.appendChild(E("td", null, ou(d["Température Min Mois (°C)"]) + " °C"));
      tr.appendChild(E("td", null, ou(d["Température Max Mois (°C)"]) + " °C"));
      tr.appendChild(E("td", null, ou(d["Température Moyenne Mois (°C)"]) + " °C"));
      var tj = d["Température Jour (°C)"];
      tr.appendChild(E("td", null, (tj && tj.max != null)
        ? (tj.max + " / " + ou(tj.min) + " °C") : (d.message || "--")));
      tr.appendChild(E("td", null, d["Précipitations Jour (mm)"] != null
        ? d["Précipitations Jour (mm)"] + " mm" : "--"));
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    wrap.appendChild(table);
    return wrap;
  }

  var graphique = null;
  function dessiner(jour) {
    var canvas = document.getElementById("mj-canvas");
    if (!canvas || typeof Chart === "undefined") return;
    if (graphique) { graphique.destroy(); graphique = null; }
    var h = jour.heures || [];
    graphique = new Chart(canvas.getContext("2d"), {
      type: "line",
      data: {
        labels: h.map(function (x) { return x.heure; }),
        datasets: [
          { label: "Temperature (°C)", data: h.map(function (x) { return x.temperature_c; }),
            borderColor: "#dc3232", yAxisID: "y1", tension: 0.3, fill: false },
          { label: "WBGT (°C)", data: h.map(function (x) { return x.wbgt_c; }),
            borderColor: "#f59628", yAxisID: "y1", tension: 0.3, fill: false, borderDash: [5, 4] },
          { label: "Pluie (mm)", data: h.map(function (x) { return x.pluie_mm; }),
            borderColor: "#3987e5", backgroundColor: "rgba(57,135,229,0.25)",
            yAxisID: "y2", type: "bar" },
          { label: "Rafale (km/h)", data: h.map(function (x) { return x.vent_rafale_kmh; }),
            borderColor: "#008300", yAxisID: "y3", tension: 0.3, fill: false }
        ]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: { legend: { labels: { boxWidth: 12, font: { size: 11 } } } },
        scales: {
          y1: { type: "linear", position: "left", title: { display: true, text: "°C" } },
          y2: { type: "linear", position: "right", beginAtZero: true,
                title: { display: true, text: "mm" }, grid: { display: false } },
          // Le vent a sa propre echelle, masquee : superposer trois grandeurs
          // sur deux axes rendrait la lecture fausse.
          y3: { type: "linear", display: false, beginAtZero: true }
        }
      }
    });
  }

  // La modale peut etre fermee par plusieurs chemins (croix d'origine, clic
  // sur l'overlay, Echap). On surcharge donc aussi la fermeture, plutot que de
  // ne nettoyer que depuis notre propre bouton.
  var fermerOriginal = window.closeMeteoModal;
  if (typeof fermerOriginal === "function") {
    window.closeMeteoModal = function () {
      var m = document.getElementById("meteoModal");
      if (m) m.classList.remove("meteo-modal-large");
      if (graphique) { graphique.destroy(); graphique = null; }
      return fermerOriginal.apply(this, arguments);
    };
  }

  window.openMeteoModal = function (date) {
    var modal = document.getElementById("meteoModal");
    var overlay = document.getElementById("modalOverlay");
    var hote = document.getElementById("meteo-details");
    if (!modal || !overlay || !hote) return originale.apply(this, arguments);

    modal.classList.add("meteo-modal-large");
    hote.textContent = "";
    hote.appendChild(E("div", "mj-entete", "Chargement..."));
    overlay.classList.add("show");
    modal.classList.add("show");
    modal.setAttribute("aria-hidden", "false");
    document.documentElement.style.overflow = "hidden";
    document.body.style.overflow = "hidden";

    Promise.all([
      fetch("/api/meteo/analyse").then(function (r) { return r.json(); }),
      fetch("/historique_meteo/" + encodeURIComponent(date))
        .then(function (r) { return r.json(); }).catch(function () { return null; })
    ]).then(function (res) {
      var jour = ((res[0] || {}).jours || []).filter(function (j) { return j.date === date; })[0];
      if (!jour) {
        // Repli sur la modale d'origine plutot qu'un ecran vide : la date
        // demandee peut sortir de l'horizon de /api/meteo/analyse.
        modal.classList.remove("meteo-modal-large");
        return originale.call(window, date);
      }
      construire(date, jour, res[1]);
    }).catch(function (e) {
      console.error("[Meteo] modale du jour:", e);
      modal.classList.remove("meteo-modal-large");
      originale.call(window, date);
    });
  };
})();
