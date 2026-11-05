/* =====================================================================
   Tuiles IGN (Geoplateforme) sur toutes les cartes de Cockpit.
   Sans ce fichier, une tuile refusee (IGN sature ou lent) reste un trou tant
   que la carte n'est pas rechargee : Leaflet ne la redemande jamais.

   A charger juste apres leaflet.js : le crochet s'applique a toute couche dont
   l'URL vise data.geopf.fr, sans rien changer dans le code des cartes.
     - la tuile est redemandee apres 2 s, 4 s, 8 s... (30 s au plus, avec un
       peu de hasard pour ne pas tout redemander d'un coup) ;
     - en attendant, la couche ACO est posee sous l'IGN et se voit a travers
       le trou. Elle est retiree une fois tous les trous combles.
   Option secoursAco: false pour une carte qui a deja un fond dessous (murs TV).
   ===================================================================== */
(function () {
  if (!window.L || !L.TileLayer) return;

  var MOTIF_IGN = /data\.geopf\.fr\/wmts/;
  var ESSAIS_MAX = 8;
  var ATTENTE_MAX_MS = 30000;
  var DELAI_RETRAIT_MS = 1000; // laisse a la tuile IGN le temps d'apparaitre en fondu

  function urlAco() {
    // Les tablettes Field n'ont pas de session sur /tiles
    return location.pathname.indexOf("/field") === 0
      ? "/field/resources/tiles/{z}/{x}/{y}.png"
      : "/tiles/{z}/{x}/{y}.png";
  }

  function attente(essai) {
    return Math.min(1000 * Math.pow(2, essai), ATTENTE_MAX_MS) * (0.75 + Math.random() * 0.5);
  }

  L.TileLayer.addInitHook(function () {
    if (!MOTIF_IGN.test(this._url || "")) return;
    var couche = this, aco = null, retrait = null, enEchec = new Set();

    function poserAco() {
      clearTimeout(retrait);
      if (aco || couche.options.secoursAco === false || !couche._map) return;
      aco = L.tileLayer(urlAco(), {
        attribution: "ACO", tms: true, maxZoom: couche.options.maxZoom, pane: couche.options.pane,
      }).addTo(couche._map);
      aco.bringToBack();
    }
    function retirerAco() {
      clearTimeout(retrait);
      if (aco) aco.remove();
      aco = null;
    }
    function oublier(ev) {
      if (!enEchec.delete(ev.tile) || enEchec.size) return;
      clearTimeout(retrait);
      retrait = setTimeout(retirerAco, DELAI_RETRAIT_MS);
    }

    couche.on("tileerror", function (ev) {
      var t = ev.tile;
      // URL gardee a la premiere erreur : getTileUrl suit le zoom courant, pas celui de la tuile
      if (!t.urlIgn) t.urlIgn = t.src;
      t.essaisIgn = (t.essaisIgn || 0) + 1;
      enEchec.add(t);
      poserAco();
      if (t.essaisIgn > ESSAIS_MAX) return;
      var essai = t.essaisIgn;
      setTimeout(function () {
        if (t.isConnected) t.src = t.urlIgn + "&essai=" + essai;
      }, attente(essai));
    });
    couche.on("tileload tileunload tileabort", oublier);
    couche.on("remove", function () {
      enEchec.clear();
      retirerAco();
    });
  });
})();
