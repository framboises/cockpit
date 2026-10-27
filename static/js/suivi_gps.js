/* suivi_gps.js -- page /suivi-gps (Surveillance, suivi GPS operateur).
 *
 * Donnees : /api/balises/suivi (balises_mur.etat_mur, tous les groupes
 * visibles ou ?groupes=), toutes les 5 s. Meme modele que le mur TV
 * /balises-mur : en ligne / en mouvement par balises_core, activite = statut
 * de la tablette Field ou fiche PC Org ouverte qui nomme la balise.
 *
 * Lien profond : ?unite=<id de balise> selectionne et centre l'unite.
 *
 * Groupes affiches : puces en tete de liste, memorises dans l'adresse
 * (?groupes=) et dans ce navigateur (localStorage suiviGps.*). Sans choix :
 * tous les groupes visibles.
 */
(function () {
  "use strict";

  var RAFRAICHIR_MS = 5000;
  var MUETTE_RECENTE_S = 12 * 3600;   // au-dela : unite rangee, repliee en fin de groupe
  var CENTRE = [47.938561, 0.224318];
  var CLE = "suiviGps.";
  var ACT = {
    sur_place: { l: "Sur place", c: "#3b82f6", rang: 0 },
    engagee:   { l: "Engagée", c: "#f59e0b", rang: 1 },
    fin:       { l: "Fin d'inter", c: "#a855f7", rang: 2 },
    libre:     { l: "Libre", c: "#22c55e", rang: 3 },
    pause:     { l: "Pause", c: "#94a3b8", rang: 4 }
  };
  var SOURCES = { anoloc: "Anoloc", teltonika: "Teltonika", field: "Tablette Field" };
  var FILTRES = [
    { id: "tous", l: "Toutes", c: "#64748b", f: function () { return true; } },
    { id: "engagees", l: "Engagées", c: "#f59e0b", f: function (b) { return engagee(b); } },
    { id: "libres", l: "Libres", c: "#22c55e", f: function (b) { return b.activite === "libre"; } },
    { id: "mouvement", l: "En mouvement", c: "#0284c7", f: function (b) { return b.mouvement; } },
    { id: "hors", l: "Hors ligne", c: "#94a3b8", f: function (b) { return !b.en_ligne; } }
  ];

  var params = new URLSearchParams(location.search);
  var S = {
    data: null, recu: 0, erreur: null, decalage: 0,
    groupes: null,            // null = tous les groupes visibles
    filtre: "tous", q: "", sel: null, suivre: false,
    noms: memoire("noms") !== "0", traces: memoire("traces") !== "0",
    fond: memoire("fond") === "ortho" ? "ortho" : "plan",
    plies: lireJson("plies", {}), anciennes: {},
    marqueurs: {}, cadre: false
  };
  var carte, coucheTraces, coucheBalises, couchesFond = [];

  /* ---------------- utilitaires ---------------- */
  function $(id) { return document.getElementById(id); }
  function el(tag, cls, txt) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (txt != null) e.textContent = txt;
    return e;
  }
  function ico(nom, taille) {
    var s = el("span", "material-symbols-outlined", nom);
    if (taille) s.style.fontSize = taille + "px";
    return s;
  }
  function memoire(cle, v) {
    try {
      if (v === undefined) return localStorage.getItem(CLE + cle) || "";
      localStorage.setItem(CLE + cle, v);
    } catch (e) {}
    return "";
  }
  function lireJson(cle, defaut) {
    try { return JSON.parse(memoire(cle)) || defaut; } catch (e) { return defaut; }
  }
  function couleurSure(c) { return /^#[0-9a-fA-F]{3,8}$/.test(c || "") ? c : "#6366f1"; }
  function iconeSure(i) { return /^[a-z0-9_]{1,40}$/.test(i || "") ? i : "location_on"; }
  function engagee(b) { return b.activite === "engagee" || b.activite === "sur_place" || b.activite === "fin"; }
  function maintenant() { return Date.now() + S.decalage; }
  function age(s) {
    if (s == null) return "jamais vue";
    if (s < 60) return Math.max(0, Math.round(s)) + " s";
    var m = Math.round(s / 60);
    if (m < 60) return m + " min";
    var h = Math.floor(m / 60);
    if (h < 48) return h + " h" + (m % 60 && h < 10 ? " " + ("0" + (m % 60)).slice(-2) : "");
    return Math.floor(h / 24) + " j";
  }
  function depuis(iso) { return iso ? age((maintenant() - new Date(iso).getTime()) / 1000) : ""; }
  function hms(iso) {
    if (!iso) return "--";
    var d = new Date(iso);
    return ("0" + d.getHours()).slice(-2) + ":" + ("0" + d.getMinutes()).slice(-2) + ":" + ("0" + d.getSeconds()).slice(-2);
  }
  function cardinal(cap) {
    var p = ["N", "NE", "E", "SE", "S", "SO", "O", "NO"];
    return p[Math.round(((+cap || 0) % 360) / 45) % 8];
  }
  function norm(t) {
    return String(t || "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");
  }
  function batFaible(b) { return b.batterie != null && b.batterie <= 20 && !b.charge; }
  function aPosition(b) { return b.lat != null && b.lng != null; }

  /* ---------------- groupes choisis ---------------- */
  function lireChoix() {
    var brut = params.get("groupes");
    if (brut == null) brut = memoire("groupes");
    if (!brut) return null;
    var out = [];
    brut.split(",").forEach(function (x) { x = x.trim(); if (x && out.indexOf(x) < 0) out.push(x); });
    return out.length ? out : null;
  }
  function ecrireChoix() {
    var txt = S.groupes ? S.groupes.join(",") : "";
    memoire("groupes", txt);
    if (txt) params.set("groupes", txt); else params.delete("groupes");
    var q = params.toString().replace(/%2C/g, ",");
    history.replaceState(null, "", location.pathname + (q ? "?" + q : ""));
    majLienMur();
  }
  function groupesAffiches() {
    var d = S.data;
    if (!d) return [];
    return d.groupes || [];
  }
  function basculerGroupe(id) {
    var dispo = (S.data && S.data.dispo || []).map(function (o) { return o.valeur; });
    var sel = S.groupes ? S.groupes.slice() : dispo.slice();
    var i = sel.indexOf(id);
    if (i >= 0) sel.splice(i, 1); else sel.push(id);
    // Ordre de la configuration, "tous" si tout est coche
    sel = dispo.filter(function (g) { return sel.indexOf(g) >= 0; });
    S.groupes = !sel.length || sel.length === dispo.length ? null : sel;
    ecrireChoix();
    S.cadre = false;
    charger(true);
  }
  function majLienMur() {
    var a = $("sg-mur");
    var ids = S.groupes || (S.data && S.data.dispo || []).map(function (o) { return o.valeur; });
    a.href = "/balises-mur?groupes=" + ids.slice(0, 3).map(encodeURIComponent).join(",") + "&fond=" + S.fond;
  }

  /* ---------------- carte ---------------- */
  function reessayer(couche) {
    couche.on("tileerror", function (ev) {
      var t = ev.tile, n = +(t.getAttribute("data-essais") || 0) + 1;
      if (n > 5) return;
      t.setAttribute("data-essais", n);
      var src = couche.getTileUrl(ev.coords);
      setTimeout(function () { t.src = src + (src.indexOf("?") < 0 ? "?" : "&") + "essai=" + n; }, 2000 * n);
    });
    return couche;
  }
  function poserFond(fond) {
    S.fond = fond === "ortho" ? "ortho" : "plan";
    memoire("fond", S.fond);
    document.body.classList.toggle("sg-ortho", S.fond === "ortho");
    couchesFond.forEach(function (c) { carte.removeLayer(c); });
    if (S.fond === "ortho") {
      couchesFond = [
        L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
          { maxNativeZoom: 19, maxZoom: 20, zIndex: 1 }),
        reessayer(L.tileLayer("https://data.geopf.fr/wmts?SERVICE=WMTS&VERSION=1.0.0&REQUEST=GetTile&LAYER=ORTHOIMAGERY.ORTHOPHOTOS&STYLE=normal&TILEMATRIXSET=PM&FORMAT=image/jpeg&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}",
          { attribution: "IGN-F/Geoplateforme", maxNativeZoom: 19, maxZoom: 20, zIndex: 2 }))];
    } else {
      couchesFond = [reessayer(L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
        { attribution: "&copy; OpenStreetMap", maxNativeZoom: 19, maxZoom: 20, zIndex: 1 }))];
    }
    couchesFond.forEach(function (c) { c.addTo(carte); });
    document.querySelectorAll("#sg-fond button").forEach(function (b) {
      b.classList.toggle("active", b.getAttribute("data-fond") === S.fond);
    });
    majLienMur();
  }
  function initCarte() {
    carte = L.map("sg-map", { center: CENTRE, zoom: 14, maxZoom: 20, zoomControl: true });
    coucheTraces = L.layerGroup().addTo(carte);
    coucheBalises = L.layerGroup().addTo(carte);
    poserFond(S.fond);
    carte.on("dragstart", function () { if (S.suivre) { S.suivre = false; rendreDetail(); } });
    // Grille posee apres la creation de la carte (polices, barre laterale) :
    // sans ce recalcul, les tuiles ne couvrent qu'une partie du cadre
    if (window.ResizeObserver) new ResizeObserver(function () { carte.invalidateSize(); }).observe($("sg-map"));
  }

  function toutes() {
    var out = [];
    groupesAffiches().forEach(function (g) {
      g.balises.forEach(function (b) { out.push({ b: b, g: g }); });
    });
    return out;
  }
  function visible(b) {
    var f = FILTRES.filter(function (x) { return x.id === S.filtre; })[0] || FILTRES[0];
    if (!f.f(b)) return false;
    if (!S.q) return true;
    return norm(b.label + " " + (b.fiche_texte || "")).indexOf(S.q) >= 0;
  }

  function iconeBalise(o) {
    var b = o.b, c = couleurSure(o.g.couleur), a = ACT[b.activite];
    var taille = b.mouvement ? 30 : b.en_ligne ? 26 : 20;
    var html = '<div class="sg-bal' + (b.en_ligne ? "" : " off") + (engagee(b) ? " eng" : "") +
      (S.sel === b.id ? " sel" : "") + '" style="--c:' + c + (a ? ";--a:" + a.c : "") + '">' +
      (b.mouvement ? '<div class="cap" style="transform:rotate(' + (+b.cap || 0) + 'deg)"></div>' : "") +
      (a ? '<div class="act"></div>' : "") +
      '<div class="rond"><span class="material-symbols-outlined" style="font-size:' + Math.round(taille * 0.55) + 'px">' +
      (b.tablette ? "tablet_android" : iconeSure(o.g.icone)) + "</span></div></div>";
    return L.divIcon({ className: "", html: html, iconSize: [taille, taille], iconAnchor: [taille / 2, taille / 2] });
  }

  function dessinerCarte() {
    var vus = {};
    coucheTraces.clearLayers();
    toutes().forEach(function (o) {
      var b = o.b;
      if (!aPosition(b) || !visible(b)) return;
      vus[b.id] = true;
      if (S.traces && b.en_ligne && b.trace && b.trace.length > 1) {
        var tr = b.trace.concat([[b.lat, b.lng]]), n = tr.length;
        for (var i = 1; i < n; i++) {
          L.polyline([tr[i - 1], tr[i]], { color: couleurSure(o.g.couleur), weight: 4, opacity: 0.15 + 0.7 * i / n,
            lineCap: "round", interactive: false }).addTo(coucheTraces);
        }
      }
      var cle = [b.en_ligne, b.mouvement, b.cap, b.vitesse, b.activite, o.g.couleur, S.sel === b.id, S.noms].join("|");
      var m = S.marqueurs[b.id];
      var z = S.sel === b.id ? 5000 : engagee(b) ? 4000 : b.mouvement ? 3000 : b.en_ligne ? 2000 : 0;
      if (!m) {
        m = L.marker([b.lat, b.lng], { icon: iconeBalise(o), zIndexOffset: z, keyboard: false }).addTo(coucheBalises);
        m.on("click", (function (id) { return function () { selectionner(id, false); }; })(b.id));
        S.marqueurs[b.id] = m;
      } else {
        m.setLatLng([b.lat, b.lng]);
        if (m._cle !== cle) { m.setIcon(iconeBalise(o)); m.setZIndexOffset(z); }
      }
      if (m._cle !== cle) {
        m.unbindTooltip();
        // Texte en noeuds : Leaflet interprete une chaine comme du HTML
        var t = el("span", "", b.label);
        if (b.mouvement) t.appendChild(el("b", "", b.vitesse + " km/h"));
        if (engagee(b) || b.activite === "pause") {
          var tag = el("i", "", ACT[b.activite].l);
          tag.style.setProperty("--a", ACT[b.activite].c);
          t.appendChild(tag);
        }
        m.bindTooltip(t, { permanent: S.noms && (b.en_ligne || engagee(b)), direction: "bottom",
          offset: [0, b.mouvement ? 15 : 13], className: "sg-nom" });
        var tip = m.getTooltip();
        if (tip && tip.getElement && tip.getElement()) tip.getElement().style.setProperty("--c", couleurSure(o.g.couleur));
        m._cle = cle;
      }
    });
    Object.keys(S.marqueurs).forEach(function (id) {
      if (!vus[id]) { coucheBalises.removeLayer(S.marqueurs[id]); delete S.marqueurs[id]; }
    });
    if (!S.cadre) S.cadre = recadrer(true);
    var s = selection();
    if (S.suivre && s && aPosition(s.b)) carte.panTo([s.b.lat, s.b.lng], { animate: true });
  }

  function recadrer(silencieux, liste) {
    var pts = (liste || toutes().filter(function (o) { return visible(o.b); }))
      .filter(function (o) { return aPosition(o.b) && (liste || o.b.en_ligne); })
      .map(function (o) { return [o.b.lat, o.b.lng]; });
    if (!pts.length) return false;
    if (pts.length === 1) carte.setView(pts[0], 16, { animate: !silencieux });
    else carte.fitBounds(pts, { padding: [50, 50], maxZoom: 17, animate: !silencieux });
    return true;
  }

  /* ---------------- selection ---------------- */
  function selection() {
    if (!S.sel) return null;
    var t = toutes();
    for (var i = 0; i < t.length; i++) if (t[i].b.id === S.sel) return t[i];
    return null;
  }
  function selectionner(id, centrer) {
    S.sel = S.sel === id && !centrer ? null : id;
    if (!S.sel) S.suivre = false;
    var s = selection();
    if (s && centrer !== false && aPosition(s.b)) carte.setView([s.b.lat, s.b.lng], Math.max(carte.getZoom(), 16));
    rendreTout();
  }

  /* ---------------- liste ---------------- */
  function rendreGroupes() {
    var box = $("sg-groupes"), d = S.data;
    box.textContent = "";
    if (!d) return;
    var tous = el("button", "sg-gpuce tous", S.groupes ? "Tous les groupes" : "Tous affichés");
    tous.type = "button";
    tous.title = "Afficher tous les groupes visibles";
    tous.addEventListener("click", function () { S.groupes = null; ecrireChoix(); S.cadre = false; charger(true); });
    box.appendChild(tous);
    (d.dispo || []).forEach(function (o) {
      var on = !S.groupes || S.groupes.indexOf(o.valeur) >= 0;
      var b = el("button", "sg-gpuce" + (on ? " on" : ""));
      b.type = "button";
      b.style.setProperty("--c", couleurSure(o.couleur));
      b.title = o.nb + " balise" + (o.nb > 1 ? "s" : "");
      var ic = el("span", "ic"); ic.appendChild(ico(iconeSure(o.icone))); b.appendChild(ic);
      b.appendChild(document.createTextNode(o.libelle));
      b.addEventListener("click", function () { basculerGroupe(o.valeur); });
      box.appendChild(b);
    });
  }

  function rendreFiltres() {
    var box = $("sg-filtres"), t = toutes();
    box.textContent = "";
    FILTRES.forEach(function (f) {
      var n = t.filter(function (o) { return f.f(o.b); }).length;
      var b = el("button", "sg-filtre" + (S.filtre === f.id ? " on" : ""));
      b.type = "button";
      b.style.setProperty("--c", f.c);
      b.appendChild(el("i"));
      b.appendChild(document.createTextNode(f.l + " "));
      b.appendChild(el("b", "", String(n)));
      b.addEventListener("click", function () { S.filtre = f.id; rendreTout(); });
      box.appendChild(b);
    });
  }

  function ordre(a, b) {
    var ra = (ACT[a.activite] || { rang: 5 }).rang, rb = (ACT[b.activite] || { rang: 5 }).rang;
    if (a.en_ligne !== b.en_ligne && !engagee(a) && !engagee(b)) return a.en_ligne ? -1 : 1;
    if (ra !== rb) return ra - rb;
    if (a.mouvement !== b.mouvement) return a.mouvement ? -1 : 1;
    if (!a.en_ligne && !b.en_ligne) return (a.age_s == null ? 1e12 : a.age_s) - (b.age_s == null ? 1e12 : b.age_s);
    return a.label.localeCompare(b.label, "fr", { numeric: true });
  }

  function ligne(b, g) {
    var a = ACT[b.activite];
    var r = el("div", "sg-u" + (b.en_ligne ? "" : " off") + (engagee(b) ? " eng" : "") + (S.sel === b.id ? " sel" : ""));
    if (a) r.style.setProperty("--a", a.c);
    r.appendChild(el("span", "pt"));
    var nm = el("span", "nm");
    if (b.tablette) nm.appendChild(ico("tablet_android"));
    nm.appendChild(document.createTextNode(b.label));
    r.appendChild(nm);
    var dr = el("span", "dr");
    if (batFaible(b)) {
      var bat = el("span", "bat" + (b.batterie <= 10 ? " crit" : ""));
      bat.appendChild(ico("battery_alert"));
      bat.appendChild(document.createTextNode(Math.round(b.batterie) + "%"));
      dr.appendChild(bat);
    }
    if (b.en_ligne) {
      if (b.mouvement) dr.appendChild(el("span", "vit", b.vitesse + " km/h"));
      else dr.appendChild(el("span", "arr", b.gps ? "arrêt" : "sans GPS"));
    }
    if (a) {
      var bd = el("span", "bd" + (b.activite === "libre" ? " libre" : ""), a.l + (b.activite !== "libre" && b.activite_depuis ? " " + depuis(b.activite_depuis) : ""));
      dr.appendChild(bd);
    }
    r.appendChild(dr);
    var sous = el("div", "sous");
    if (engagee(b) && b.fiche_texte) sous.appendChild(el("span", "fi", b.fiche_texte));
    else if (b.tablette) sous.appendChild(el("span", "", "Tablette Field"));
    else sous.appendChild(el("span", "", SOURCES[b.source] || b.source || ""));
    sous.appendChild(el("span", "ag", b.en_ligne ? "vue il y a " + age(b.age_s) : "muette " + age(b.age_s)));
    r.appendChild(sous);
    r.title = g.libelle + " - " + b.label;
    r.addEventListener("click", function () { selectionner(b.id, true); });
    return r;
  }

  function rendreListe() {
    var box = $("sg-blocs"), haut = box.scrollTop;
    box.textContent = "";
    var gs = groupesAffiches();
    if (S.data && !S.data.actif) {
      box.appendChild(el("div", "sg-vide", "Le suivi GPS est désactivé (Configuration > Balises GPS)."));
      return;
    }
    if (S.data && S.data.ignores && S.data.ignores.length) {
      box.appendChild(el("div", "sg-vide", "Groupe(s) introuvable(s) ou non visible(s) : " + S.data.ignores.join(", ")));
    }
    var rien = true;
    gs.forEach(function (g) {
      var unites = g.balises.filter(visible).sort(ordre);
      if (!unites.length && (S.filtre !== "tous" || S.q)) return;
      rien = false;
      var bloc = el("section", "sg-bloc" + (S.plies[g.id] ? " plie" : ""));
      bloc.style.setProperty("--c", couleurSure(g.couleur));
      var t = el("div", "sg-bloc-tete");
      var ic = el("span", "ic"); ic.appendChild(ico(iconeSure(g.icone))); t.appendChild(ic);
      t.appendChild(el("span", "nom", g.libelle));
      var c = el("span", "cpt");
      if (g.engagees) c.appendChild(el("span", "e", g.engagees + " eng."));
      if (g.libres) c.appendChild(el("span", "l", g.libres + " libre" + (g.libres > 1 ? "s" : "")));
      var n = el("span", "n", g.en_ligne + "/" + g.total);
      n.title = "En ligne / total";
      c.appendChild(n);
      t.appendChild(c);
      var z = el("button", "zoom");
      z.type = "button";
      z.title = "Centrer la carte sur ce groupe";
      z.appendChild(ico("center_focus_strong", 18));
      z.addEventListener("click", function (ev) {
        ev.stopPropagation();
        recadrer(false, g.balises.filter(function (b) { return aPosition(b) && visible(b); }).map(function (b) { return { b: b }; }));
      });
      t.appendChild(z);
      t.appendChild(el("span", "material-symbols-outlined caret", "expand_more"));
      t.addEventListener("click", function () {
        if (S.plies[g.id]) delete S.plies[g.id]; else S.plies[g.id] = true;
        memoire("plies", JSON.stringify(S.plies));
        bloc.classList.toggle("plie");
      });
      bloc.appendChild(t);
      var ls = el("div", "sg-unites");
      // Muettes depuis longtemps (ou jamais vues) : repliees, sauf filtre Hors ligne
      var anciennes = S.filtre === "hors" || S.q ? [] : unites.filter(function (b) {
        return !b.en_ligne && !engagee(b) && (b.age_s == null || b.age_s >= MUETTE_RECENTE_S);
      });
      unites.forEach(function (b) {
        if (anciennes.indexOf(b) >= 0 && !S.anciennes[g.id]) return;
        ls.appendChild(ligne(b, g));
      });
      if (anciennes.length) {
        var bt = el("button", "sg-anciennes");
        bt.type = "button";
        bt.appendChild(ico(S.anciennes[g.id] ? "expand_less" : "expand_more"));
        bt.appendChild(document.createTextNode(S.anciennes[g.id] ? "Masquer les unités rangées"
          : anciennes.length + " hors ligne depuis plus de 12 h ou jamais vue" + (anciennes.length > 1 ? "s" : "")));
        bt.addEventListener("click", function () {
          S.anciennes[g.id] = !S.anciennes[g.id];
          rendreListe();
        });
        ls.appendChild(bt);
      }
      if (!g.balises.length) ls.appendChild(el("div", "sg-vide", "Aucune balise dans ce groupe"));
      bloc.appendChild(ls);
      box.appendChild(bloc);
    });
    if (rien && S.data) box.appendChild(el("div", "sg-vide", S.q || S.filtre !== "tous" ? "Aucune unité ne correspond." : "Aucun groupe de balises."));
    box.scrollTop = haut;
  }

  /* ---------------- en-tete ---------------- */
  function rendreTotaux() {
    var box = $("sg-totaux");
    box.textContent = "";
    var t = { eng: 0, lib: 0, en: 0, tot: 0, mv: 0 };
    groupesAffiches().forEach(function (g) {
      t.eng += g.engagees || 0; t.lib += g.libres || 0; t.en += g.en_ligne; t.tot += g.total; t.mv += g.en_mouvement;
    });
    [[t.eng, "engagées", "#b45309"], [t.lib, "libres", "#15803d"], [t.en + "/" + t.tot, "en ligne", ""],
     [t.mv, "en mouvement", "#0284c7"], [t.tot - t.en, "hors ligne", "#64748b"]].forEach(function (x) {
      var s = el("span", "sg-tot");
      if (x[2]) s.style.setProperty("--c", x[2]);
      s.appendChild(el("b", "", String(x[0])));
      s.appendChild(document.createTextNode(x[1]));
      box.appendChild(s);
    });
  }

  function rendreFlux() {
    var l = $("sg-live"), a = (Date.now() - S.recu) / 1000;
    var ok = S.recu && a < 30 && !S.erreur;
    l.className = "sg-live " + (ok ? "is-ok" : S.recu || S.erreur ? "is-error" : "");
    $("sg-live-text").textContent = ok ? "En direct" : S.erreur ? "Données arrêtées" : "Connexion...";
    if (S.recu) l.title = "Dernière mise à jour " + hms(new Date(S.recu).toISOString());
  }

  /* ---------------- detail ---------------- */
  function caseInfo(k, v, petit, large) {
    var c = el("div", "sg-d-case" + (large ? " large" : ""));
    c.appendChild(el("div", "k", k));
    var vv = el("div", "v", v);
    if (petit) vv.appendChild(el("small", "", petit));
    c.appendChild(vv);
    return c;
  }
  function bouton(icone, texte, on, fn, titre) {
    var b = el("button", "sg-d-btn" + (on ? " on" : ""));
    b.type = "button";
    if (titre) b.title = titre;
    b.appendChild(ico(icone));
    b.appendChild(document.createTextNode(texte));
    b.addEventListener("click", fn);
    return b;
  }

  function rendreDetail() {
    var box = $("sg-detail");
    box.textContent = "";
    var s = selection();
    box.classList.toggle("ouvert", !!s);
    if (!s) { rendreSynthese(box); return; }
    var b = s.b, g = s.g, a = ACT[b.activite];
    var tete = el("div", "sg-d-tete");
    tete.style.setProperty("--c", couleurSure(g.couleur));
    var ic = el("span", "ic"); ic.appendChild(ico(b.tablette ? "tablet_android" : iconeSure(g.icone), 22)); tete.appendChild(ic);
    var tx = el("div", "");
    tx.appendChild(el("h3", "", b.label));
    tx.appendChild(el("div", "grp", g.libelle + " · " + (SOURCES[b.source] || b.source || "")));
    tete.appendChild(tx);
    var f = el("button", "sg-icon-btn fermer");
    f.type = "button"; f.title = "Fermer"; f.appendChild(ico("close"));
    f.addEventListener("click", function () { S.sel = null; S.suivre = false; rendreTout(); });
    tete.appendChild(f);
    box.appendChild(tete);

    var act = el("div", "sg-d-act");
    act.style.setProperty("--a", a ? a.c : "#94a3b8");
    var t = el("div", "t", a ? a.l : "Hors ligne");
    if (a && b.activite_depuis) t.appendChild(el("small", "", "depuis " + depuis(b.activite_depuis) + " (" + hms(b.activite_depuis).slice(0, 5) + ")"));
    act.appendChild(t);
    if (engagee(b) && b.fiche_texte) act.appendChild(el("div", "f", b.fiche_texte));
    act.appendChild(el("div", "s", b.tablette ? "Statut déclaré par la tablette Field"
      : engagee(b) ? "Fiche PC Org ouverte qui engage ce véhicule"
      : a ? "Aucune fiche ouverte ne l'engage" : "Pas de position récente, aucune fiche ouverte"));
    box.appendChild(act);

    var gr = el("div", "sg-d-grille");
    gr.appendChild(caseInfo("Liaison", b.en_ligne ? "En ligne" : "Hors ligne", b.en_ligne ? "" : age(b.age_s)));
    gr.appendChild(caseInfo("Vitesse", b.en_ligne ? (b.mouvement ? b.vitesse + " km/h" : "Arrêt") : "--",
      b.mouvement ? "cap " + cardinal(b.cap) : ""));
    gr.appendChild(caseInfo("Batterie", b.batterie != null ? Math.round(b.batterie) + " %" : "--", b.charge ? "en charge" : ""));
    gr.appendChild(caseInfo("GPS", b.gps ? "Position OK" : "Sans GPS"));
    gr.appendChild(caseInfo("Dernière trame", hms(b.vu_a), b.age_s != null ? "il y a " + age(b.age_s) : ""));
    gr.appendChild(caseInfo("Réseau", b.gsm != null ? b.gsm + " / 5" : "--"));
    if (aPosition(b)) gr.appendChild(caseInfo("Position", (+b.lat).toFixed(5) + ", " + (+b.lng).toFixed(5), "", true));
    box.appendChild(gr);

    var ac = el("div", "sg-d-actions");
    if (aPosition(b)) {
      ac.appendChild(bouton("center_focus_strong", "Centrer", false, function () {
        carte.setView([b.lat, b.lng], Math.max(carte.getZoom(), 17));
      }));
      ac.appendChild(bouton("my_location", S.suivre ? "Suivi actif" : "Suivre", S.suivre, function () {
        S.suivre = !S.suivre;
        if (S.suivre) carte.setView([b.lat, b.lng], Math.max(carte.getZoom(), 16));
        rendreDetail();
      }, "Garder la carte centrée sur cette unité"));
      var gm = el("a", "sg-d-btn");
      gm.href = "https://www.google.com/maps?q=" + (+b.lat) + "," + (+b.lng);
      gm.target = "_blank"; gm.rel = "noopener";
      gm.appendChild(ico("open_in_new"));
      gm.appendChild(document.createTextNode("Google Maps"));
      ac.appendChild(gm);
    }
    box.appendChild(ac);
  }

  function rendreSynthese(box) {
    var gs = groupesAffiches();
    box.appendChild(el("div", "sg-d-titre", "Synthèse des groupes"));
    var tab = el("table", "sg-synth");
    var th = el("thead"), tr = el("tr");
    ["Groupe", "En ligne", "Mouv.", "Eng.", "Libres", "Batt."].forEach(function (x) { tr.appendChild(el("th", "", x)); });
    th.appendChild(tr); tab.appendChild(th);
    var tb = el("tbody"), tot = [0, 0, 0, 0, 0, 0];
    gs.forEach(function (g) {
      var bf = g.balises.filter(batFaible).length;
      var r = el("tr");
      r.style.setProperty("--c", couleurSure(g.couleur));
      var c0 = el("td"); c0.appendChild(el("span", "pt")); c0.appendChild(document.createTextNode(g.libelle)); r.appendChild(c0);
      r.appendChild(el("td", "", g.en_ligne + "/" + g.total));
      r.appendChild(el("td", g.en_mouvement ? "" : "z", String(g.en_mouvement)));
      r.appendChild(el("td", g.engagees ? "e" : "z", String(g.engagees || 0)));
      r.appendChild(el("td", g.libres ? "l" : "z", String(g.libres || 0)));
      r.appendChild(el("td", bf ? "w" : "z", String(bf)));
      r.title = "Centrer la carte sur " + g.libelle;
      r.addEventListener("click", function () {
        recadrer(false, g.balises.filter(aPosition).map(function (b) { return { b: b }; }));
      });
      tb.appendChild(r);
      tot[0] += g.en_ligne; tot[1] += g.total; tot[2] += g.en_mouvement; tot[3] += g.engagees || 0; tot[4] += g.libres || 0; tot[5] += bf;
    });
    tab.appendChild(tb);
    var tf = el("tfoot"), fr = el("tr");
    [["Total", ""], [tot[0] + "/" + tot[1], ""], [tot[2], ""], [tot[3], "e"], [tot[4], "l"], [tot[5], tot[5] ? "w" : "z"]].forEach(function (x) {
      fr.appendChild(el("td", x[1], String(x[0])));
    });
    tf.appendChild(fr); tab.appendChild(tf);
    box.appendChild(tab);

    // A surveiller : engagees muettes, batteries critiques, decrochages recents
    var alertes = [];
    toutes().forEach(function (o) {
      var b = o.b;
      if (engagee(b) && !b.en_ligne) alertes.push({ o: o, crit: true, t: b.label + " engagée mais muette depuis " + age(b.age_s), i: "wifi_off" });
      else if (b.en_ligne && batFaible(b)) alertes.push({ o: o, crit: b.batterie <= 10, t: b.label + " : batterie " + Math.round(b.batterie) + " %", i: "battery_alert" });
      else if (!b.en_ligne && b.age_s != null && b.age_s < 3600) alertes.push({ o: o, crit: false, t: b.label + " muette depuis " + age(b.age_s), i: "signal_disconnected" });
    });
    alertes.sort(function (a, b) { return (b.crit ? 1 : 0) - (a.crit ? 1 : 0); });
    box.appendChild(el("div", "sg-d-titre", "À surveiller" + (alertes.length ? " (" + alertes.length + ")" : "")));
    var la = el("div", "sg-alertes");
    if (!alertes.length) la.appendChild(el("div", "sg-vide", "Rien à signaler."));
    alertes.slice(0, 30).forEach(function (x) {
      var d = el("div", "sg-alerte" + (x.crit ? " crit" : ""));
      d.appendChild(ico(x.i));
      d.appendChild(document.createTextNode(x.t));
      d.addEventListener("click", function () { selectionner(x.o.b.id, true); });
      la.appendChild(d);
    });
    box.appendChild(la);
    box.appendChild(el("div", "sg-d-titre", "Cliquez une unité pour sa fiche détaillée"));
  }

  /* ---------------- boucle ---------------- */
  function rendreTout() {
    if (!S.data) return;
    rendreGroupes();
    rendreFiltres();
    rendreListe();
    rendreTotaux();
    rendreDetail();
    dessinerCarte();
  }

  var minuteur = null;
  function charger(immediat) {
    if (minuteur) { clearTimeout(minuteur); minuteur = null; }
    var q = new URLSearchParams();
    if (S.groupes) q.set("groupes", S.groupes.join(","));
    if (params.get("event") && params.get("year")) { q.set("event", params.get("event")); q.set("year", params.get("year")); }
    var qs = q.toString();
    fetch("/api/balises/suivi" + (qs ? "?" + qs : ""), { credentials: "same-origin", cache: "no-store" })
      .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
      .then(function (d) {
        S.decalage = new Date(d.maintenant).getTime() - Date.now();
        S.data = d; S.recu = Date.now(); S.erreur = null;
        // Lien profond ?unite=<id> (alerte, autre page) : une seule fois
        var u = params.get("unite");
        if (u) {
          params.delete("unite");
          S.sel = u;
          var o = selection();
          if (o && aPosition(o.b)) { S.cadre = true; carte.setView([o.b.lat, o.b.lng], 16); }
        }
        rendreTout();
        majLienMur();
      })
      .catch(function (e) { S.erreur = e; })
      .then(function () { rendreFlux(); minuteur = setTimeout(charger, RAFRAICHIR_MS); });
  }

  /* ---------------- evenements ---------------- */
  document.querySelectorAll("#sg-fond button").forEach(function (b) {
    b.addEventListener("click", function () { poserFond(b.getAttribute("data-fond")); });
  });
  function bascule(id, cle) {
    var b = $(id);
    b.classList.toggle("active", S[cle]);
    b.addEventListener("click", function () {
      S[cle] = !S[cle];
      memoire(cle, S[cle] ? "1" : "0");
      b.classList.toggle("active", S[cle]);
      Object.keys(S.marqueurs).forEach(function (k) { S.marqueurs[k]._cle = null; });
      dessinerCarte();
    });
  }
  bascule("sg-noms", "noms");
  bascule("sg-traces", "traces");
  $("sg-recadrer").addEventListener("click", function () { S.suivre = false; recadrer(false); rendreDetail(); });
  $("sg-q").addEventListener("input", function (e) { S.q = norm(e.target.value.trim()); rendreListe(); dessinerCarte(); });
  document.addEventListener("keydown", function (e) {
    var dansChamp = /^(INPUT|TEXTAREA|SELECT)$/.test((e.target && e.target.tagName) || "");
    if (e.key === "/" && !dansChamp) { e.preventDefault(); $("sg-q").focus(); }
    else if (e.key === "Escape") {
      if (dansChamp && $("sg-q").value) { $("sg-q").value = ""; S.q = ""; rendreListe(); dessinerCarte(); }
      else if (S.sel) { S.sel = null; S.suivre = false; rendreTout(); }
    }
  });
  setInterval(rendreFlux, 1000);

  S.groupes = lireChoix();
  initCarte();
  majLienMur();
  charger();
})();
