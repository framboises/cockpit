// ==========================================================================
// METEO VENT -- couche Vent de la carte d'accueil (meme carte que le radar)
//
// Fond colore selon la force du vent, particules animees, fleches de
// direction (grille + points cles de l'enceinte), legende, curseur temporel.
// Bascule Pluie / Vent / Rafales / Pluie + Vent : en Pluie + Vent le curseur
// est celui du radar (meteo_avance.js), le vent suit l'instant affiche.
//
// Donnees : /api/meteo/vent/sequence (ligne du temps) et /api/meteo/vent/trame
// (grilles U/V et rafales en m/s, en-tete facon grib2json). Aucune donnee
// meteo n'est inventee ici : on interpole seulement entre les points de grille.
// Aucun innerHTML avec des donnees.
// ==========================================================================
(function () {
  "use strict";

  var API = {
    sequence: "/api/meteo/vent/sequence",
    trame: "/api/meteo/vent/trame?id="
  };
  var RAFRAICHISSEMENT_MS = 15 * 60 * 1000;
  var LECTURE_MS = 750;
  var ESPACEMENT_FLECHES_PX = 110;

  // Palette type Windy, en m/s -> RGB.
  var PALETTE = [
    [0, [98, 113, 183]], [1, [57, 97, 159]], [3, [74, 148, 169]], [5, [77, 141, 123]],
    [7, [83, 165, 83]], [9, [53, 159, 53]], [11, [167, 157, 81]], [13, [159, 127, 58]],
    [15, [161, 108, 92]], [17, [129, 58, 78]], [19, [175, 80, 136]], [21, [117, 74, 147]],
    [24, [109, 97, 163]], [27, [68, 105, 141]], [29, [92, 144, 152]], [36, [125, 68, 165]],
    [46, [231, 215, 215]]
  ];
  var GRADUATIONS_MS = [0, 3, 5, 9, 13, 17, 21, 27, 36];

  // A partir de ce zoom, plus de fond colore en Vent / Rafales : il masquait
  // la carte de l'enceinte ; particules et fleches suffisent a lire le vent.
  var FOND_ZOOM_MAX = 18;

  // Nuages : couverture totale AROME (%) -> gris bleute de plus en plus opaque
  var NUAGES_RGB = [72, 84, 102];
  var NUAGES_ALPHA_MAX = 0.62;
  var NUAGES_SEUIL = 5;

  var MODES = [
    { cle: "pluie", libelle: "Pluie" },
    { cle: "vent", libelle: "Vent" },
    { cle: "rafales", libelle: "Rafales" },
    { cle: "nuages", libelle: "Nuages" },
    { cle: "pluie_vent", libelle: "Pluie + Vent" }
  ];

  var vent = {
    actif: false, mode: "vent", seq: null, index: 0, cache: {}, champ: null,
    fond: null, fleches: null, particules: null, timer: null, rafraichissement: null,
    unite: lireUnite(), gen: 0
  };

  // ------------------------------------------------------------- utilitaires
  function el(tag, cls, texte) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (texte !== undefined && texte !== null) n.textContent = String(texte);
    return n;
  }
  function carte() {
    return (window.CockpitMapView && window.CockpitMapView.getMap) ? window.CockpitMapView.getMap() : null;
  }
  function get(url) {
    return fetch(url, { credentials: "same-origin" }).then(function (r) {
      if (!r.ok) throw new Error(url + " -> " + r.status);
      return r.json();
    });
  }
  function toast(msg, type) { if (window.showToast) window.showToast(msg, type || "error"); }
  function lireUnite() {
    try { return localStorage.getItem("cockpit_vent_unite") === "kt" ? "kt" : "kmh"; } catch (e) { return "kmh"; }
  }
  function ecrireUnite(u) {
    try { localStorage.setItem("cockpit_vent_unite", u); } catch (e) { /* stockage indisponible */ }
  }
  function convertir(ms) {
    if (ms === null || ms === undefined || isNaN(ms)) return null;
    return Math.round(vent.unite === "kt" ? ms * 1.943844 : ms * 3.6);
  }
  function libelleUnite() { return vent.unite === "kt" ? "nœuds" : "km/h"; }
  function directionDeg(u, v) { return (270 - Math.atan2(v, u) * 180 / Math.PI + 360) % 360; }
  var CARDINAUX = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSO", "SO", "OSO", "O", "ONO", "NO", "NNO"];
  function cardinal(deg) { return CARDINAUX[Math.round(deg / 22.5) % 16]; }

  var FMT_HEURE = { hour: "2-digit", minute: "2-digit", timeZone: "Europe/Paris" };
  var FMT_JOUR = { weekday: "short", day: "2-digit", timeZone: "Europe/Paris" };
  function heure(iso) { var d = new Date(iso); return isNaN(d) ? "--:--" : d.toLocaleTimeString("fr-FR", FMT_HEURE); }
  function heureEtJour(iso) {
    var d = new Date(iso);
    if (isNaN(d)) return "--:--";
    var auj = new Date().toLocaleDateString("fr-FR", FMT_JOUR);
    var j = d.toLocaleDateString("fr-FR", FMT_JOUR);
    return (j === auj ? "" : j + " ") + d.toLocaleTimeString("fr-FR", FMT_HEURE);
  }

  function couleur(ms) {
    if (!(ms >= 0)) return null;
    for (var i = 1; i < PALETTE.length; i++) {
      if (ms <= PALETTE[i][0]) {
        var a = PALETTE[i - 1], b = PALETTE[i];
        var t = (ms - a[0]) / (b[0] - a[0]);
        return [0, 1, 2].map(function (k) { return Math.round(a[1][k] + (b[1][k] - a[1][k]) * t); });
      }
    }
    return PALETTE[PALETTE.length - 1][1].slice();
  }

  // ------------------------------------------------------------- champ
  // Echantillonnage bilineaire d'une grille (lignes nord -> sud) en lat/lon.
  function Champ(d, mode) {
    this.h = d.header;
    this.d = d;
    var gust = (mode === "rafales" && d.u_rafale) || (!d.u && d.u_rafale);
    this.u = gust ? d.u_rafale : d.u;
    this.v = gust ? d.v_rafale : d.v;
    this.vecteurRafale = !!gust;
    this.intensite = mode === "rafales" ? d.rafale : null;
    // Couverture nuageuse (%), seulement en mode Nuages
    this.nuages = mode === "nuages" ? d.nuages : null;
  }
  Champ.prototype.interp = function (grille, lat, lon) {
    if (!grille) return null;
    var h = this.h;
    var gx = (lon - h.lo1) / h.dx, gy = (h.la1 - lat) / h.dy;
    if (gx < -0.5 || gy < -0.5 || gx > h.nx - 0.5 || gy > h.ny - 0.5) return null;
    gx = Math.max(0, Math.min(h.nx - 1.0001, gx));
    gy = Math.max(0, Math.min(h.ny - 1.0001, gy));
    var x0 = Math.floor(gx), y0 = Math.floor(gy), fx = gx - x0, fy = gy - y0;
    var i = y0 * h.nx + x0;
    var a = grille[i], b = grille[i + 1], c = grille[i + h.nx], e = grille[i + h.nx + 1];
    if (a === null || b === null || c === null || e === null) return null;
    return a * (1 - fx) * (1 - fy) + b * fx * (1 - fy) + c * (1 - fx) * fy + e * fx * fy;
  };
  Champ.prototype.vecteur = function (lat, lon) {
    var u = this.interp(this.u, lat, lon), v = this.interp(this.v, lat, lon);
    return (u === null || v === null) ? null : [u, v];
  };
  // Force affichee : le vent moyen, ou la rafale en mode Rafales.
  Champ.prototype.force = function (lat, lon) {
    if (this.intensite) return this.interp(this.intensite, lat, lon);
    var w = this.vecteur(lat, lon);
    return w ? Math.sqrt(w[0] * w[0] + w[1] * w[1]) : null;
  };
  Champ.prototype.bornes = function () {
    var h = this.h;
    return [[h.la2 - h.dy / 2, h.lo1 - h.dx / 2], [h.la1 + h.dy / 2, h.lo2 + h.dx / 2]];
  };

  // ------------------------------------------------------------- fond colore
  function imageFond(ch) {
    var h = ch.h, k = 8;
    var cv = document.createElement("canvas");
    cv.width = h.nx * k;
    cv.height = h.ny * k;
    var ctx = cv.getContext("2d");
    var img = ctx.createImageData(cv.width, cv.height);
    for (var py = 0; py < cv.height; py++) {
      var lat = h.la1 + h.dy / 2 - (py + 0.5) / k * h.dy;
      for (var px = 0; px < cv.width; px++) {
        var lon = h.lo1 - h.dx / 2 + (px + 0.5) / k * h.dx;
        var o = (py * cv.width + px) * 4;
        if (ch.nuages) {
          // Nuages : teinte fixe, opacite proportionnelle a la couverture
          var n = ch.interp(ch.nuages, lat, lon);
          if (n === null || n < NUAGES_SEUIL) { img.data[o + 3] = 0; continue; }
          img.data[o] = NUAGES_RGB[0]; img.data[o + 1] = NUAGES_RGB[1]; img.data[o + 2] = NUAGES_RGB[2];
          img.data[o + 3] = Math.round(Math.min(100, n) / 100 * NUAGES_ALPHA_MAX * 255);
          continue;
        }
        var c = couleur(ch.force(lat, lon));
        if (!c) { img.data[o + 3] = 0; continue; }
        img.data[o] = c[0]; img.data[o + 1] = c[1]; img.data[o + 2] = c[2]; img.data[o + 3] = 255;
      }
    }
    ctx.putImageData(img, 0, 0);
    return cv.toDataURL("image/png");
  }

  function majFond() {
    var m = carte();
    if (!m) return;
    var nuages = vent.mode === "nuages";
    var voir = vent.champ && vent.mode !== "pluie_vent" &&
      (nuages ? !!vent.champ.nuages : m.getZoom() < FOND_ZOOM_MAX);
    if (!voir) {
      if (vent.fond) { m.removeLayer(vent.fond); vent.fond = null; }
      return;
    }
    var url = imageFond(vent.champ);
    // Nuages : l'opacite est dans l'image (fonction de la couverture)
    var opacite = nuages ? 1 : 0.6;
    if (!vent.fond) {
      vent.fond = L.imageOverlay(url, vent.champ.bornes(), { opacity: opacite, interactive: false, zIndex: 440 }).addTo(m);
    } else {
      vent.fond.setUrl(url);
      vent.fond.setOpacity(opacite);
      vent.fond.setBounds(L.latLngBounds(vent.champ.bornes()));
    }
  }

  // ------------------------------------------------------------- particules
  function Particules(m) {
    this.m = m;
    if (!m.getPane("ventParticules")) {
      var p = m.createPane("ventParticules");
      p.style.zIndex = 455;
      p.style.pointerEvents = "none";
    }
    this.cv = document.createElement("canvas");
    this.cv.className = "vent-particules";
    m.getPane("ventParticules").appendChild(this.cv);
    this.ctx = this.cv.getContext("2d");
    this.liste = [];
    this.anim = null;
    var self = this;
    this.surDebut = function () { self.arreter(); self.effacer(); };
    this.surFin = function () { self.demarrer(); };
    m.on("movestart zoomstart", this.surDebut);
    m.on("moveend zoomend resize", this.surFin);
  }
  Particules.prototype.effacer = function () { this.ctx.clearRect(0, 0, this.cv.width, this.cv.height); };
  Particules.prototype.arreter = function () {
    if (this.anim) { cancelAnimationFrame(this.anim); this.anim = null; }
  };
  Particules.prototype.detruire = function () {
    this.arreter();
    this.m.off("movestart zoomstart", this.surDebut);
    this.m.off("moveend zoomend resize", this.surFin);
    if (this.cv.parentNode) this.cv.parentNode.removeChild(this.cv);
  };
  Particules.prototype.demarrer = function () {
    this.arreter();
    // Nuages : ni particules ni fleches, la couverture seule
    if (vent.mode === "nuages") { this.effacer(); return; }
    if (!vent.champ || document.hidden) return;
    var m = this.m, taille = m.getSize();
    this.cv.width = taille.x;
    this.cv.height = taille.y;
    L.DomUtil.setPosition(this.cv, m.containerPointToLayerPoint([0, 0]));
    // Projection lineaire de l'ecran : a l'echelle de l'enceinte l'erreur
    // de Mercator est invisible, et l'animation evite 2 000 appels Leaflet
    // par image.
    var no = m.containerPointToLatLng([0, 0]), se = m.containerPointToLatLng([taille.x, taille.y]);
    this.proj = { lat0: no.lat, lon0: no.lng, dlat: (se.lat - no.lat) / taille.y, dlon: (se.lng - no.lng) / taille.x };
    var b = vent.champ.bornes();
    var p1 = m.latLngToContainerPoint([b[1][0], b[0][1]]), p2 = m.latLngToContainerPoint([b[0][0], b[1][1]]);
    this.zone = {
      x0: Math.max(0, p1.x), y0: Math.max(0, p1.y),
      x1: Math.min(taille.x, p2.x), y1: Math.min(taille.y, p2.y)
    };
    if (this.zone.x1 <= this.zone.x0 || this.zone.y1 <= this.zone.y0) return;
    var surface = (this.zone.x1 - this.zone.x0) * (this.zone.y1 - this.zone.y0);
    var n = Math.min(2600, Math.max(300, Math.round(surface / 650)));
    this.liste = [];
    for (var i = 0; i < n; i++) this.liste.push(this.naitre({}));
    var self = this;
    var boucle = function () { self.pas(); self.anim = requestAnimationFrame(boucle); };
    this.anim = requestAnimationFrame(boucle);
  };
  Particules.prototype.naitre = function (p) {
    var z = this.zone;
    p.x = z.x0 + Math.random() * (z.x1 - z.x0);
    p.y = z.y0 + Math.random() * (z.y1 - z.y0);
    p.age = Math.floor(Math.random() * 80);
    return p;
  };
  Particules.prototype.pas = function () {
    var ctx = this.ctx, ch = vent.champ, pr = this.proj;
    if (!ch) return;
    // Trainee : on estompe l'image precedente au lieu de l'effacer.
    ctx.globalCompositeOperation = "destination-in";
    ctx.fillStyle = "rgba(0, 0, 0, 0.95)";
    ctx.fillRect(0, 0, this.cv.width, this.cv.height);
    ctx.globalCompositeOperation = "source-over";
    ctx.lineWidth = ch.vecteurRafale ? 1.5 : 1.1;
    ctx.strokeStyle = "rgba(255, 255, 255, 0.78)";
    ctx.beginPath();
    // Vitesse d'ecran constante quel que soit le zoom, proportionnelle au vent.
    var k = 0.3;
    for (var i = 0; i < this.liste.length; i++) {
      var p = this.liste[i];
      if (p.age++ > 90) { this.naitre(p); p.age = 0; continue; }
      var w = ch.vecteur(pr.lat0 + p.y * pr.dlat, pr.lon0 + p.x * pr.dlon);
      if (!w) { this.naitre(p); continue; }
      var nx = p.x + w[0] * k, ny = p.y - w[1] * k;
      ctx.moveTo(p.x, p.y);
      ctx.lineTo(nx, ny);
      p.x = nx; p.y = ny;
      if (nx < this.zone.x0 || nx > this.zone.x1 || ny < this.zone.y0 || ny > this.zone.y1) this.naitre(p);
    }
    ctx.stroke();
  };

  // ------------------------------------------------------------- fleches
  var SVG = "http://www.w3.org/2000/svg";
  function iconeFleche(deg, ms, cle, libelle) {
    // La fleche montre ou VA le vent (direction meteo + 180), comme Windy.
    // SVG trace vers le nord : l'icone "navigation" de Material est penchee
    // a 45 degres et faussait toutes les directions.
    var div = el("div", "vent-fleche" + (cle ? " vent-fleche-cle" : ""));
    var taille = Math.round((cle ? 26 : 15) + Math.min(10, (ms || 0) * 0.6));
    var svg = document.createElementNS(SVG, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("width", taille);
    svg.setAttribute("height", taille);
    svg.setAttribute("class", "vent-fleche-ico");
    svg.style.transform = "rotate(" + Math.round((deg + 180) % 360) + "deg)";
    var trace = document.createElementNS(SVG, "path");
    trace.setAttribute("d", cle ? "M12 1 L20 22 L12 17 L4 22 Z" : "M12 2 L12 22 M12 2 L6 9 M12 2 L18 9");
    svg.appendChild(trace);
    div.appendChild(svg);
    if (libelle) div.appendChild(el("span", "vent-fleche-etiquette", libelle));
    return L.divIcon({ className: "vent-fleche-conteneur", html: div, iconSize: [0, 0] });
  }

  function majFleches() {
    var m = carte();
    if (!m) return;
    if (!vent.fleches) {
      if (!m.getPane("ventFleches")) {
        var p = m.createPane("ventFleches");
        p.style.zIndex = 460;
        p.style.pointerEvents = "none";
      }
      vent.fleches = L.layerGroup().addTo(m);
    }
    vent.fleches.clearLayers();
    var ch = vent.champ;
    if (!ch || vent.mode === "nuages") return;
    var taille = m.getSize(), pas = ESPACEMENT_FLECHES_PX;
    for (var y = pas / 2; y < taille.y; y += pas) {
      for (var x = pas / 2; x < taille.x; x += pas) {
        var ll = m.containerPointToLatLng([x, y]);
        var w = ch.vecteur(ll.lat, ll.lng);
        if (!w) continue;
        var ms = Math.sqrt(w[0] * w[0] + w[1] * w[1]);
        vent.fleches.addLayer(L.marker(ll, {
          icon: iconeFleche(directionDeg(w[0], w[1]), ms, false, null), interactive: false, pane: "ventFleches"
        }));
      }
    }
    // Points cles : fleche renforcee et etiquette moyen / rafale.
    var moyen = ch.d.u ? new Champ(ch.d, "vent") : null;
    var rafale = new Champ(ch.d, "rafales");
    ((vent.seq && vent.seq.points_cles) || []).forEach(function (pt) {
      var w = (moyen || ch).vecteur(pt.lat, pt.lon);
      if (!w) return;
      var ms = Math.sqrt(w[0] * w[0] + w[1] * w[1]);
      var r = rafale.force(pt.lat, pt.lon);
      var lib = pt.nom + " " + (moyen ? convertir(ms) : "--") + " / " + (r === null ? "--" : convertir(r)) + " " + libelleUnite();
      vent.fleches.addLayer(L.marker([pt.lat, pt.lon], {
        icon: iconeFleche(directionDeg(w[0], w[1]), ms, true, lib), interactive: false, pane: "ventFleches"
      }));
    });
  }

  // ------------------------------------------------------------- trames
  function chargerSequence() {
    return get(API.sequence).then(function (d) {
      vent.seq = d;
      return d;
    });
  }

  function chargerTrame(i) {
    var t = vent.seq && vent.seq.trames[i];
    if (!t) return Promise.resolve(null);
    if (vent.cache[t.id]) return Promise.resolve(vent.cache[t.id]);
    return get(API.trame + encodeURIComponent(t.id)).then(function (d) {
      vent.cache[t.id] = d;
      return d;
    });
  }

  function afficherTrame(i) {
    var n = vent.seq ? vent.seq.trames.length : 0;
    if (!n) return Promise.resolve();
    i = ((i % n) + n) % n;
    vent.index = i;
    var gen = ++vent.gen;
    return chargerTrame(i).then(function (d) {
      if (gen !== vent.gen || !vent.actif || !d) return;
      vent.champ = new Champ(d, vent.mode);
      majFond();
      majFleches();
      if (vent.particules) vent.particules.demarrer();
      majBarre();
      // Precharge la suivante : la lecture ne doit pas attendre le reseau.
      chargerTrame((i + 1) % n).catch(function () { /* sera retentee */ });
    }).catch(function (e) {
      console.warn("[Vent] trame:", e);
    });
  }

  function indexProche(iso) {
    var cible = new Date(iso).getTime(), meilleur = 0, ecart = Infinity;
    (vent.seq ? vent.seq.trames : []).forEach(function (t, i) {
      var e = Math.abs(new Date(t.valid_at).getTime() - cible);
      if (e < ecart) { ecart = e; meilleur = i; }
    });
    return meilleur;
  }

  // ------------------------------------------------------------- barre
  function boutonsModes() {
    var seg = el("div", "vent-modes");
    MODES.forEach(function (md) {
      var b = el("button", "vent-mode", md.libelle);
      b.type = "button";
      b.setAttribute("data-mode", md.cle);
      b.addEventListener("click", function () { choisirMode(md.cle); });
      seg.appendChild(b);
    });
    return seg;
  }

  function majModes() {
    var actuel = vent.actif ? vent.mode : (window.CockpitMeteoRadar && window.CockpitMeteoRadar.actif() ? "pluie" : null);
    document.querySelectorAll(".vent-mode").forEach(function (b) {
      b.classList.toggle("active", b.getAttribute("data-mode") === actuel);
    });
  }

  function legendeNuages() {
    var l = el("div", "radar-legende vent-legende");
    l.appendChild(el("span", "radar-legende-titre vent-legende-unite", "Nuages"));
    var echelle = el("div", "vent-legende-echelle");
    var degrade = el("div", "vent-legende-degrade");
    var rgb = NUAGES_RGB.join(",");
    degrade.style.background = "linear-gradient(to right, rgba(" + rgb + ",0.04), rgba(" + rgb + "," + NUAGES_ALPHA_MAX + "))";
    degrade.style.border = "1px solid rgba(148,163,184,0.4)";
    echelle.appendChild(degrade);
    var graduations = el("div", "vent-legende-graduations");
    [0, 25, 50, 75, 100].forEach(function (p) {
      var g = el("span", "radar-legende-valeur", p + " %");
      g.style.left = p + "%";
      graduations.appendChild(g);
    });
    echelle.appendChild(graduations);
    l.appendChild(echelle);
    return l;
  }

  // La legende suit le mode : force du vent ou couverture nuageuse
  function majLegende() {
    var vieille = document.querySelector("#vent-barre .vent-legende");
    if (vieille) vieille.parentNode.replaceChild(vent.mode === "nuages" ? legendeNuages() : legende(), vieille);
  }

  function legende() {
    var l = el("div", "radar-legende vent-legende");
    l.appendChild(el("span", "radar-legende-titre vent-legende-unite", libelleUnite()));
    var echelle = el("div", "vent-legende-echelle");
    var max = PALETTE[PALETTE.length - 1][0];
    var stops = PALETTE.map(function (p) {
      return "rgb(" + p[1].join(",") + ") " + (p[0] / max * 100).toFixed(1) + "%";
    });
    var degrade = el("div", "vent-legende-degrade");
    degrade.style.background = "linear-gradient(to right, " + stops.join(", ") + ")";
    echelle.appendChild(degrade);
    var graduations = el("div", "vent-legende-graduations");
    GRADUATIONS_MS.forEach(function (ms) {
      var g = el("span", "radar-legende-valeur", convertir(ms));
      g.style.left = (ms / max * 100) + "%";
      graduations.appendChild(g);
    });
    echelle.appendChild(graduations);
    l.appendChild(echelle);
    var u = el("button", "vent-unite", vent.unite === "kt" ? "km/h" : "nœuds");
    u.type = "button";
    u.title = "Changer d'unité";
    u.addEventListener("click", function () {
      vent.unite = vent.unite === "kt" ? "kmh" : "kt";
      ecrireUnite(vent.unite);
      majLegende();
      majFleches();
      majBarre();
    });
    l.appendChild(u);
    return l;
  }

  function construireBarre() {
    var barre = document.getElementById("vent-barre");
    if (barre) { barre.style.display = vent.mode === "pluie_vent" ? "none" : "flex"; return; }
    var conteneur = document.getElementById("cockpit-map");
    if (!conteneur) return;
    barre = el("div", "radar-barre vent-barre");
    barre.id = "vent-barre";

    var haut = el("div", "radar-ligne");
    haut.appendChild(boutonsModes());
    haut.appendChild(el("span", "vent-observation"));
    barre.appendChild(haut);

    var controles = el("div", "radar-ligne");
    var play = el("button", "radar-btn");
    play.id = "vent-play";
    play.title = "Lecture";
    play.appendChild(el("span", "material-symbols-outlined", "play_arrow"));
    play.addEventListener("click", jouer);
    var pause = el("button", "radar-btn active");
    pause.id = "vent-pause";
    pause.title = "Pause";
    pause.appendChild(el("span", "material-symbols-outlined", "pause"));
    pause.addEventListener("click", arreter);
    var curseur = document.createElement("input");
    curseur.type = "range";
    curseur.className = "radar-curseur vent-curseur";
    curseur.min = 0;
    curseur.addEventListener("input", function () { arreter(); afficherTrame(parseInt(curseur.value, 10)); });
    var infos = el("div", "radar-infos vent-infos");
    infos.appendChild(el("span", "radar-heure vent-heure", "--:--"));
    infos.appendChild(el("span", "radar-type vent-source", ""));
    infos.appendChild(el("span", "radar-max vent-circuit", ""));
    controles.appendChild(play);
    controles.appendChild(pause);
    controles.appendChild(curseur);
    controles.appendChild(infos);
    barre.appendChild(controles);

    var bas = el("div", "radar-ligne radar-ligne-bas");
    bas.appendChild(vent.mode === "nuages" ? legendeNuages() : legende());
    bas.appendChild(el("span", "radar-source", "Source : Météo-France (AROME-PI, AROME)"));
    barre.appendChild(bas);
    conteneur.appendChild(barre);
    if (window.L && L.DomEvent) {
      L.DomEvent.disableClickPropagation(barre);
      L.DomEvent.disableScrollPropagation(barre);
    }
    barre.style.display = vent.mode === "pluie_vent" ? "none" : "flex";
  }

  function texteCircuit() {
    var ch = vent.champ;
    if (!ch) return "";
    var lat = 47.945, lon = 0.224;
    if (vent.mode === "nuages") {
      var n = new Champ(ch.d, "nuages").interp(ch.d.nuages, lat, lon);
      return n === null ? "Couverture nuageuse indisponible pour cette heure" : "Circuit : nuages " + Math.round(n) + " %";
    }
    var moyen = ch.d.u ? new Champ(ch.d, "vent") : null;
    var w = moyen ? moyen.vecteur(lat, lon) : null;
    var r = new Champ(ch.d, "rafales").force(lat, lon);
    var morceaux = [];
    if (w) morceaux.push(convertir(Math.sqrt(w[0] * w[0] + w[1] * w[1])) + " " + libelleUnite() + " " + cardinal(directionDeg(w[0], w[1])));
    if (r !== null) morceaux.push("rafales " + convertir(r));
    return morceaux.length ? "Circuit : " + morceaux.join(", ") : "";
  }

  function majBarre() {
    var t = vent.seq && vent.seq.trames[vent.index];
    var barre = document.getElementById("vent-barre");
    if (barre && t) {
      barre.querySelector(".vent-heure").textContent = heureEtJour(t.valid_at);
      var src = barre.querySelector(".vent-source");
      // Nuages : toujours AROME (interpole sur les trames AROME-PI)
      src.textContent = vent.mode === "nuages"
        ? (t.source === "AROME-PI" ? "AROME interpolé" : "AROME · run " + heure(t.run_at))
        : t.source + " · run " + heure(t.run_at) +
          (t.source === "AROME-PI" && vent.mode !== "rafales" ? " · moyen AROME" : "");
      src.style.color = t.source === "AROME-PI" ? "#f59628" : "#3987e5";
      var maxi = t.rafale_max_site_kmh;
      barre.querySelector(".vent-circuit").textContent = texteCircuit() +
        (vent.mode !== "nuages" && maxi !== null && maxi !== undefined ? " · max enceinte " + convertir(maxi / 3.6) : "");
      var c = barre.querySelector(".vent-curseur");
      c.max = Math.max(0, vent.seq.trames.length - 1);
      c.value = vent.index;
    }
    var obs = vent.seq && vent.seq.observation;
    var txt = obs
      ? "Mesuré Le Mans " + heure(obs.valid_at) + " : " +
        (obs.vent_kmh !== null ? convertir(obs.vent_kmh / 3.6) + " " + libelleUnite() + " " + (obs.direction || "") : "--") +
        (obs.rafale_kmh !== null ? ", rafale " + convertir(obs.rafale_kmh / 3.6) : "")
      : "Mesure de station indisponible";
    document.querySelectorAll(".vent-observation").forEach(function (n) { n.textContent = txt; });
    // Ligne vent dans la barre du radar (mode Pluie + Vent)
    var info = document.getElementById("radar-vent-info");
    if (info) {
      info.textContent = (vent.actif && vent.mode === "pluie_vent" && t)
        ? "Vent " + heure(t.valid_at) + " (" + t.source + ") · " + texteCircuit().replace("Circuit : ", "") : "";
    }
    majModes();
  }

  function majBoutonsLecture() {
    var enCours = !!vent.timer;
    var p = document.getElementById("vent-play"), s = document.getElementById("vent-pause");
    if (p) p.classList.toggle("active", enCours);
    if (s) s.classList.toggle("active", !enCours);
  }
  function jouer() {
    arreter();
    vent.timer = setInterval(function () { afficherTrame(vent.index + 1); }, LECTURE_MS);
    majBoutonsLecture();
  }
  function arreter() {
    if (vent.timer) { clearInterval(vent.timer); vent.timer = null; }
    majBoutonsLecture();
  }

  // Les boutons de mode sont aussi poses dans la barre du radar.
  function greffeBarreRadar() {
    var rb = document.getElementById("radar-barre");
    if (!rb || rb.querySelector(".vent-modes")) return;
    var ligne = el("div", "radar-ligne");
    ligne.appendChild(boutonsModes());
    var info = el("span", "vent-radar-info");
    info.id = "radar-vent-info";
    ligne.appendChild(info);
    rb.insertBefore(ligne, rb.firstChild);
    majModes();
  }

  // ------------------------------------------------------------- bascule
  function arreterVent() {
    var m = carte();
    arreter();
    if (vent.rafraichissement) { clearInterval(vent.rafraichissement); vent.rafraichissement = null; }
    if (vent.particules) { vent.particules.detruire(); vent.particules = null; }
    if (m && vent.fond) { m.removeLayer(vent.fond); vent.fond = null; }
    if (m && vent.fleches) { m.removeLayer(vent.fleches); vent.fleches = null; }
    if (m) m.off("zoomend", majFond);
    vent.actif = false;
    vent.champ = null;
    var b = document.getElementById("vent-barre");
    if (b) b.style.display = "none";
    majBoutonVent();
    majBarre();
  }

  function demarrerVent() {
    var m = carte();
    if (!m) return Promise.resolve(false);
    return chargerSequence().then(function (d) {
      if (!d.trames || !d.trames.length) {
        toast("Aucune prévision de vent disponible");
        return false;
      }
      vent.actif = true;
      if (!vent.particules) vent.particules = new Particules(m);
      // Fond colore masque au zoom rapproche (FOND_ZOOM_MAX)
      m.off("zoomend", majFond);
      m.on("zoomend", majFond);
      construireBarre();
      majBoutonVent();
      if (vent.rafraichissement) clearInterval(vent.rafraichissement);
      vent.rafraichissement = setInterval(rafraichir, RAFRAICHISSEMENT_MS);
      return true;
    }).catch(function (e) {
      console.error("[Vent] sequence:", e);
      toast("Couche vent indisponible");
      return false;
    });
  }

  function rafraichir() {
    if (!vent.actif) return;
    var courante = vent.seq && vent.seq.trames[vent.index];
    chargerSequence().then(function () {
      if (!vent.actif || !vent.seq.trames.length) return;
      vent.cache = {};
      afficherTrame(courante ? indexProche(courante.valid_at) : 0);
    }).catch(function (e) { console.warn("[Vent] rafraichissement:", e); });
  }

  function choisirMode(mode) {
    var radar = window.CockpitMeteoRadar;
    try { localStorage.setItem("cockpit_meteo_mode", mode); } catch (e) { /* stockage indisponible */ }
    if (mode === "pluie") {
      arreterVent();
      if (radar) radar.activer().then(function () { greffeBarreRadar(); majModes(); });
      return;
    }
    var ancien = vent.mode;
    vent.mode = mode;
    var suite = vent.actif ? Promise.resolve(true) : demarrerVent();
    suite.then(function (ok) {
      if (!ok) { vent.mode = ancien; majModes(); return; }
      var b = document.getElementById("vent-barre");
      if (mode === "pluie_vent") {
        arreter();
        if (b) b.style.display = "none";
        var pret = radar ? radar.activer() : Promise.resolve(false);
        pret.then(function () {
          greffeBarreRadar();
          // L'instant du radar arrive par l'evenement ; a defaut, le present.
          afficherTrame(vent.dernierRadar ? indexProche(vent.dernierRadar) : 0);
        });
      } else {
        if (radar && radar.actif()) radar.desactiver();
        if (b) b.style.display = "flex";
        majLegende();
        afficherTrame(vent.index);
      }
    });
  }

  // Bouton unique "Meteo" de la carte (pose par meteo_avance.js) : eteint
  // tout si une couche est affichee, sinon rouvre le dernier mode utilise.
  function bouton() {
    var radar = window.CockpitMeteoRadar;
    if (vent.actif || (radar && radar.actif())) {
      if (vent.actif) arreterVent();
      if (radar && radar.actif()) radar.desactiver();
      return;
    }
    var mode = "pluie";
    try { mode = localStorage.getItem("cockpit_meteo_mode") || "pluie"; } catch (e) { /* stockage indisponible */ }
    if (!MODES.some(function (m) { return m.cle === mode; })) mode = "pluie";
    choisirMode(mode);
  }

  function majBoutonVent() {
    if (window.CockpitMeteoRadar && window.CockpitMeteoRadar.majBouton) window.CockpitMeteoRadar.majBouton();
  }

  // ------------------------------------------------------------- evenements
  document.addEventListener("cockpit:radar-image", function (e) {
    vent.dernierRadar = e.detail && e.detail.valid_at;
    if (vent.actif && vent.mode === "pluie_vent" && vent.seq) {
      var i = indexProche(vent.dernierRadar);
      if (i !== vent.index || !vent.champ) afficherTrame(i);
      else majBarre();
    }
  });
  document.addEventListener("cockpit:radar-etat", function (e) {
    if (e.detail && e.detail.actif) {
      greffeBarreRadar();
      // Radar rallume par son propre bouton pendant que le vent est affiche :
      // le fond colore masquerait la pluie, on passe en Pluie + Vent.
      if (vent.actif && vent.mode !== "pluie_vent") choisirMode("pluie_vent");
    } else if (vent.actif && vent.mode === "pluie_vent") {
      arreterVent();
    }
    majModes();
  });
  document.addEventListener("visibilitychange", function () {
    if (!vent.particules) return;
    if (document.hidden) vent.particules.arreter();
    else vent.particules.demarrer();
  });

  window.CockpitMeteoVent = {
    mode: choisirMode, bouton: bouton,
    actif: function () { return vent.actif; },
    etat: function () { return vent; }
  };
})();
