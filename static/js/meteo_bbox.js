// ==========================================================================
// METEO — Admin : emprise de veille radar et fraicheur des flux
//
// Deux emprises, et elles ne servent pas a la meme chose :
//   site   (bleu)  enveloppe des zones d'exploitation, base du calcul par zone
//   veille (ambre) rayon de detection, derive du curseur, pour voir arriver
//                  les cellules avant qu'elles ne soient sur le circuit
//
// La configuration est globale, pas par evenement : le circuit ne bouge pas
// d'une epreuve a l'autre.
// ==========================================================================
(function () {
  "use strict";

  var body = document.getElementById("meteo-body");
  if (!body || typeof L === "undefined") return;

  var elVeille = document.getElementById("meteo-veille");
  var elVeilleVal = document.getElementById("meteo-veille-val");
  var elStatus = document.getElementById("meteo-status");
  var elBboxTxt = document.getElementById("meteo-bbox-txt");
  var elGrilleTxt = document.getElementById("meteo-grille-txt");
  var elMaj = document.getElementById("meteo-maj");
  var elFlux = document.getElementById("meteo-flux");
  var elHint = document.getElementById("meteo-draw-hint");
  var elMurFond = document.getElementById("meteo-mur-fond");
  var elMurTxt = document.getElementById("meteo-mur-txt");

  var carte = null;
  var rectSite = null;
  var rectVeille = null;
  var etat = { bbox_site: null, veille_km: 40, defaut: null,
               mur: null, mur_defaut: null, fonds: [] };
  var enDessin = false;
  var coinDepart = null;

  // Carte de cadrage du mur : distincte de la carte d'emprise. On y regle un
  // point de vue (centre + zoom + fond), pas une geometrie.
  var carteMur = null;
  var coucheMur = null;
  var repereMur = null;

  function csrfToken() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute("content") : "";
  }

  function toast(msg, type) {
    if (typeof window.showToast === "function") window.showToast(msg, type);
  }

  function statut(texte, couleur) {
    if (!elStatus) return;
    elStatus.textContent = texte || "";
    elStatus.style.color = couleur || "var(--muted)";
  }

  // Les libelles d'erreur du serveur sont des codes : les traduire ici evite
  // d'afficher "bbox_trop_petite" a un utilisateur.
  var ERREURS = {
    bbox_incomplete: "Emprise incomplete.",
    bbox_invalide: "Emprise invalide (coins inverses ?).",
    bbox_trop_petite: "L'emprise doit englober toutes les zones du circuit.",
    bbox_trop_grande: "Emprise trop large : la grille deviendrait inutilement lourde.",
    veille_km_invalide: "Rayon de veille illisible.",
    veille_km_hors_bornes: "Rayon de veille hors bornes (10 a 150 km).",
    mur_centre_invalide: "Centre du mur illisible.",
    mur_centre_hors_bornes: "Centre du mur hors des bornes geographiques.",
    mur_zoom_invalide: "Niveau de zoom illisible.",
    mur_zoom_hors_bornes: "Niveau de zoom hors bornes (6 a 15).",
    mur_fond_inconnu: "Fond de carte inconnu."
  };

  // ------------------------------------------------------------------------
  // Cadrage du mur
  // ------------------------------------------------------------------------

  function fondPar(cle) {
    for (var i = 0; i < etat.fonds.length; i++) {
      if (etat.fonds[i].cle === cle) return etat.fonds[i];
    }
    return etat.fonds[0] || null;
  }

  function majTexteMur() {
    if (!elMurTxt || !etat.mur) return;
    elMurTxt.textContent = "centre " + etat.mur.centre.lat.toFixed(4) + ", " +
      etat.mur.centre.lon.toFixed(4) + "  ·  zoom " + etat.mur.zoom;
  }

  function poserFondMur() {
    if (!carteMur || !etat.mur) return;
    var fond = fondPar(etat.mur.fond);
    if (!fond) return;
    if (coucheMur) carteMur.removeLayer(coucheMur);
    coucheMur = L.tileLayer(fond.url, {
      maxNativeZoom: fond.max_zoom, maxZoom: 19,
      attribution: "IGN-F/Geoplateforme"
    }).addTo(carteMur);
  }

  function initCarteMur() {
    if (!document.getElementById("meteo-mur-map") || !etat.mur) return;
    if (carteMur) {
      carteMur.invalidateSize();
      carteMur.setView([etat.mur.centre.lat, etat.mur.centre.lon], etat.mur.zoom);
      return;
    }
    // fadeAnimation desactive : au changement de fond on retire une couche pour
    // en poser une autre, et le fondu de Leaflet laissait les nouvelles tuiles a
    // opacity 0 -- chargees, mais invisibles. La carte paraissait vide.
    carteMur = L.map("meteo-mur-map", {
      zoomControl: true, attributionControl: true, fadeAnimation: false
    });
    carteMur.setView([etat.mur.centre.lat, etat.mur.centre.lon], etat.mur.zoom);
    poserFondMur();

    // Le circuit, pour savoir ce qu'on cadre.
    repereMur = L.circleMarker(
      [etat.mur_defaut.centre.lat, etat.mur_defaut.centre.lon],
      { radius: 5, color: "#f8fafc", weight: 2, fillColor: "#0ea5e9", fillOpacity: 0.9 }
    ).addTo(carteMur);

    // Le cadrage se lit sur la carte elle-meme : pas de champs a saisir, ce
    // qu'on voit ici est ce que la TV affichera.
    carteMur.on("moveend zoomend", function () {
      var c = carteMur.getCenter();
      etat.mur.centre = { lat: c.lat, lon: c.lng };
      etat.mur.zoom = carteMur.getZoom();
      majTexteMur();
      statut("Modifie, non enregistre", "#f59e0b");
    });
    majTexteMur();
  }

  function veilleDepuisRayon(km) {
    // Meme calcul que cote serveur, pour que l'apercu corresponde a ce qui
    // sera reellement collecte.
    var LAT = 47.945, LON = 0.224;
    var dlat = km / 111.32;
    var dlon = km / (111.32 * Math.cos(LAT * Math.PI / 180));
    return { west: LON - dlon, south: LAT - dlat, east: LON + dlon, north: LAT + dlat };
  }

  function bornes(b) {
    return [[b.south, b.west], [b.north, b.east]];
  }

  function dessiner() {
    if (!carte) return;
    var site = etat.bbox_site;
    var veille = veilleDepuisRayon(etat.veille_km);

    if (rectSite) carte.removeLayer(rectSite);
    if (rectVeille) carte.removeLayer(rectVeille);

    rectVeille = L.rectangle(bornes(veille), {
      color: "#f59e0b", weight: 2, fillOpacity: 0.05, dashArray: "6 6"
    }).addTo(carte);
    rectSite = L.rectangle(bornes(site), {
      color: "#3987e5", weight: 2, fillOpacity: 0.10
    }).addTo(carte);

    if (elBboxTxt) {
      elBboxTxt.textContent =
        site.west.toFixed(3) + " / " + site.south.toFixed(3) + " -> " +
        site.east.toFixed(3) + " / " + site.north.toFixed(3);
    }
    if (elGrilleTxt) {
      // Pas de 0,005 deg : la maille du radar Meteo-France fait 500 m, aller
      // plus fin ne ferait que dupliquer des pixels.
      var pas = 0.005;
      var nx = Math.ceil((veille.east - veille.west) / pas);
      var ny = Math.ceil((veille.north - veille.south) / pas);
      elGrilleTxt.textContent = ny + " x " + nx + " px  (" + etat.veille_km + " km)";
    }
  }

  function cadrer() {
    if (!carte) return;
    carte.fitBounds(bornes(veilleDepuisRayon(etat.veille_km)), { padding: [12, 12] });
  }

  function initCarte() {
    if (carte) { carte.invalidateSize(); return; }
    carte = L.map("meteo-map", { zoomControl: true, attributionControl: true });
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 18,
      attribution: "&copy; OpenStreetMap"
    }).addTo(carte);

    carte.on("click", function (e) {
      if (!enDessin) return;
      if (!coinDepart) {
        coinDepart = e.latlng;
        if (elHint) elHint.textContent = "Cliquez le coin oppose.";
        return;
      }
      var a = coinDepart, b = e.latlng;
      etat.bbox_site = {
        west: Math.min(a.lng, b.lng), east: Math.max(a.lng, b.lng),
        south: Math.min(a.lat, b.lat), north: Math.max(a.lat, b.lat)
      };
      enDessin = false;
      coinDepart = null;
      if (elHint) elHint.textContent = "";
      document.getElementById("meteo-map").style.cursor = "";
      dessiner();
      statut("Modifie, non enregistre", "#f59e0b");
    });

    dessiner();
    cadrer();
  }

  function charger() {
    fetch("/api/meteo/config")
      .then(function (r) { return r.json(); })
      .then(function (d) {
        etat.bbox_site = d.bbox_site;
        etat.veille_km = d.veille_km;
        etat.defaut = d.bbox_site_defaut;
        etat.mur = d.mur;
        etat.mur_defaut = d.mur_defaut;
        etat.fonds = d.mur_fonds || [];
        if (elMurFond) {
          elMurFond.textContent = "";
          etat.fonds.forEach(function (f) {
            var o = document.createElement("option");
            o.value = f.cle;
            o.textContent = f.libelle;
            if (f.cle === etat.mur.fond) o.selected = true;
            elMurFond.appendChild(o);
          });
        }
        initCarteMur();
        if (elVeille) elVeille.value = d.veille_km;
        if (elVeilleVal) elVeilleVal.textContent = d.veille_km + " km";
        if (elMaj) {
          elMaj.textContent = d.par_defaut
            ? "Jamais configuree (valeurs par defaut)"
            : ("Modifiee le " + String(d.updated_at || "").slice(0, 16).replace("T", " ") +
               (d.updated_by ? " par " + d.updated_by : ""));
        }
        initCarte();
      })
      .catch(function (err) {
        console.error("[Meteo] chargement config:", err);
        statut("Chargement impossible", "#ef4444");
      });
  }

  function chargerEtat() {
    if (!elFlux) return;
    fetch("/api/meteo/etat")
      .then(function (r) { return r.json(); })
      .then(function (d) {
        elFlux.textContent = "";
        (d.flux || []).forEach(function (f) {
          var couleur = f.etat === "ok" ? "#008300"
            : (f.etat === "retard" ? "#f59e0b" : "#94a3b8");
          var puce = document.createElement("div");
          puce.style.cssText =
            "display:flex; flex-direction:column; gap:2px; padding:8px 12px;" +
            "border-radius:8px; background:rgba(148,163,184,0.08);" +
            "border-left:3px solid " + couleur + "; min-width:150px;";

          var titre = document.createElement("span");
          titre.style.cssText = "font-size:0.8rem; font-weight:600;";
          titre.textContent = f.libelle;

          var detail = document.createElement("span");
          detail.style.cssText = "font-size:0.74rem; color:var(--muted);";
          if (f.etat === "absent") {
            detail.textContent = "aucune donnee";
          } else if (f.age_min !== undefined) {
            detail.textContent = f.age_min + " min" +
              (f.latence_s ? "  (latence " + Math.round(f.latence_s / 60) + " min)" : "");
          } else if (f.age_h !== undefined) {
            detail.textContent = f.age_h + " h";
          }

          puce.appendChild(titre);
          puce.appendChild(detail);
          elFlux.appendChild(puce);
        });
      })
      .catch(function (err) { console.error("[Meteo] etat:", err); });
  }

  function enregistrer() {
    fetch("/api/meteo/config", {
      method: "PUT",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken() },
      body: JSON.stringify({
        bbox_site: etat.bbox_site,
        veille_km: etat.veille_km,
        mur: etat.mur ? { centre: etat.mur.centre, zoom: etat.mur.zoom,
                          fond: etat.mur.fond } : undefined
      })
    })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
      .then(function (res) {
        if (!res.ok || res.d.ok === false) {
          var msg = ERREURS[res.d.error] || ("Erreur : " + res.d.error);
          statut(msg, "#ef4444");
          toast(msg, "error");
          return;
        }
        statut("Enregistre", "#008300");
        toast("Emprise meteo enregistree", "success");
        charger();
      })
      .catch(function (err) {
        console.error("[Meteo] enregistrement:", err);
        statut("Enregistrement impossible", "#ef4444");
      });
  }

  // --- Evenements ---------------------------------------------------------

  if (elVeille) {
    elVeille.addEventListener("input", function () {
      etat.veille_km = parseInt(elVeille.value, 10);
      if (elVeilleVal) elVeilleVal.textContent = etat.veille_km + " km";
      dessiner();
      statut("Modifie, non enregistre", "#f59e0b");
    });
    elVeille.addEventListener("change", cadrer);
  }

  var btnDraw = document.getElementById("btn-meteo-draw");
  if (btnDraw) {
    btnDraw.addEventListener("click", function () {
      enDessin = true;
      coinDepart = null;
      if (elHint) elHint.textContent = "Cliquez un premier coin sur la carte.";
      var el = document.getElementById("meteo-map");
      if (el) el.style.cursor = "crosshair";
    });
  }

  var btnReset = document.getElementById("btn-meteo-reset");
  if (btnReset) {
    btnReset.addEventListener("click", function () {
      if (!etat.defaut) return;
      etat.bbox_site = JSON.parse(JSON.stringify(etat.defaut));
      etat.veille_km = 40;
      if (elVeille) elVeille.value = 40;
      if (elVeilleVal) elVeilleVal.textContent = "40 km";
      if (etat.mur && etat.mur_defaut && carteMur) {
        etat.mur.fond = etat.mur_defaut.fond;
        if (elMurFond) elMurFond.value = etat.mur.fond;
        poserFondMur();
        carteMur.setView([etat.mur_defaut.centre.lat, etat.mur_defaut.centre.lon],
                         etat.mur_defaut.zoom);
      }
      dessiner();
      cadrer();
      statut("Defauts restaures, non enregistres", "#f59e0b");
    });
  }

  if (elMurFond) {
    elMurFond.addEventListener("change", function () {
      if (!etat.mur) return;
      etat.mur.fond = elMurFond.value;
      poserFondMur();
      statut("Modifie, non enregistre", "#f59e0b");
    });
  }

  var btnRecentrer = document.getElementById("btn-meteo-mur-recentrer");
  if (btnRecentrer) {
    btnRecentrer.addEventListener("click", function () {
      if (!carteMur || !etat.mur_defaut) return;
      // setView declenche moveend, qui met l'etat a jour et marque non enregistre.
      carteMur.setView([etat.mur_defaut.centre.lat, etat.mur_defaut.centre.lon],
                       etat.mur_defaut.zoom);
    });
  }

  var btnSave = document.getElementById("btn-meteo-save");
  if (btnSave) btnSave.addEventListener("click", enregistrer);

  // La carte est dans un bloc repliable : Leaflet doit recalculer sa taille
  // quand le bloc s'ouvre, sinon les tuiles ne se chargent qu'a moitie.
  var entete = document.querySelector('[data-section="meteo"]');
  if (entete) {
    entete.addEventListener("click", function () {
      setTimeout(function () {
        if (carte) { carte.invalidateSize(); cadrer(); }
        // La carte de cadrage est dans le meme bloc repliable : sans ce
        // recalcul, elle ne charge que le quart de ses tuiles a l'ouverture.
        if (carteMur) carteMur.invalidateSize();
      }, 220);
    });
  }

  charger();
  chargerEtat();
  setInterval(chargerEtat, 60000);
})();
