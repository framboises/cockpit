// itineraire_osm.js - Ecran "Donnees OSM" de /admin/itineraire (administrateurs)
//
// Les donnees OSM du moteur (extrait de la zone du circuit) sont figees
// jusqu'a une mise a jour. valhalla-admin telecharge un extrait a jour comme
// CANDIDAT, produit un rapport d'impact sur les regles de toutes les saisons
// et epreuves, recueille une decision par alerte, puis ACCEPTE le candidat
// (ancienne version archivee, decisions appliquees aux regles, rechargement
// du scenario actif si demande). Rien ne change avant l'acceptation.
//
// Toutes les ecritures passent par le relais /api/admin/itineraire/osm/*
// (valhalla_admin.py). Pendant une operation (job.state == "running") :
// decisions, acceptation et chargement de scenario desactives.
// Service pas a jour (GET /osm en 404) : bandeau masque, onglet explicatif.
// Utilitaires communs : window.ItAdmin (itineraire_admin.js).

(function () {
  "use strict";

  var A = window.ItAdmin;
  if (!A) return;
  var h = A.h, icon = A.icon, clear = A.clear, api = A.api, toast = A.toast, errMsg = A.errMsg, fmtDate = A.fmtDate;

  var POLL_JOB_MS = 3000;
  var POLL_REPOS_MS = 60000;

  var NIVEAUX = {
    blocking: { libelle: "Bloquant", couleur: "#dc2626", icone: "block" },
    warning: { libelle: "Attention", couleur: "#f59e0b", icone: "warning" },
    info: { libelle: "Information", couleur: "#2563eb", icone: "info" },
  };
  var ORDRE_NIVEAUX = ["blocking", "warning", "info"];
  var TYPES = {
    target_missing: ["Objet disparu", "La règle vise un objet qui n'existe plus dans les nouvelles données."],
    way_split: ["Voie découpée", "La voie qui porte la règle a été découpée en plusieurs morceaux."],
    size_drop: ["Extrait tronqué", "Le nouvel extrait est nettement plus petit que l'actuel : téléchargement incomplet possible."],
    tags_changed: ["Tags modifiés", "Les tags OSM de l'objet ont changé."],
    geometry_changed: ["Tracé modifié", "Le tracé de l'objet a changé."],
    network_changed: ["Raccordement modifié", "Le raccordement de l'objet au réseau routable a changé."],
    new_way: ["Nouvelle voie", "Voie apparue dans les nouvelles données."],
    new_barrier: ["Nouvelle barrière", "Barrière apparue dans les nouvelles données."],
  };
  var DECISIONS = {
    delete_rule: ["Supprimer la règle", "delete"],
    reassign: ["Réaffecter", "alt_route"],
    extend: ["Étendre aux morceaux", "call_split"],
    ignore: ["Ignorer", "visibility_off"],
  };
  var COUCHES = { seasons: "Saison", events: "Épreuve" };
  // Libelles des cles connues du resume du candidat ; les autres s'affichent telles quelles
  var RESUME = {
    ways_added: "voies ajoutées", ways_removed: "voies supprimées", ways_modified: "voies modifiées",
    barriers_added: "barrières ajoutées", barriers_removed: "barrières supprimées", barriers_modified: "barrières modifiées",
    ways: "voies", barriers: "barrières",
  };

  var O = {
    etat: null, indispo: false, rapport: null, sel: null, filtreType: "", filtreDoc: "",
    timer: null, carte: null, calque: null, monte: false, archives: null, reassign: {},
  };

  function $(sel, root) { return (root || document).querySelector(sel); }
  function job() { return (O.etat && O.etat.job) || {}; }
  function occupe() { return job().state === "running"; }
  function candidat() { return O.etat && O.etat.candidate; }
  function libType(t) { return (TYPES[t] || [t || "?"])[0]; }
  function docLibelle(layer, id) {
    var nom = A.docName(layer, id);
    // "Saison 2026" porte deja son type : pas de "Saison Saison 2026"
    return /^(saison|[eé]preuve)/i.test(nom) ? nom : (COUCHES[layer] || layer) + " " + nom;
  }

  // ------------------------------------------------------------------ etat

  function charger() {
    return api("GET", "/osm").then(function (res) {
      if (res.status === 404) { O.indispo = true; O.etat = null; }
      else if (res.ok) { O.indispo = false; O.etat = res.body || {}; }
      else { O.erreur = errMsg(res); }
      rendreBandeau();
      if (O.monte) rendre();
      planifier();
      return O.etat;
    }).catch(function () { planifier(); });
  }

  function planifier() {
    if (O.timer) clearTimeout(O.timer);
    O.timer = setTimeout(function () {
      if (document.hidden && !occupe()) { planifier(); return; }
      var avant = job().state;
      charger().then(function () {
        // Fin d'une operation : rapport et archives a relire
        if (avant === "running" && !occupe()) { O.rapport = null; chargerRapport(); chargerArchives(); }
      });
    }, occupe() ? POLL_JOB_MS : POLL_REPOS_MS);
  }

  // Bandeau du tableau de bord
  function rendreBandeau() {
    var b = $("#it-osm-banner");
    var badge = $("#it-osm-tabbadge");
    if (!b) return;
    clear(b);
    if (O.indispo || !O.etat) { b.hidden = true; if (badge) badge.hidden = true; return; }
    var cur = O.etat.current || {}, up = O.etat.upstream || {}, cand = candidat();
    b.hidden = false;
    b.appendChild(icon("public"));
    var txt = h("div", { className: "it-osm-banner-txt" }, [
      h("strong", { text: "Données OSM du " + (fmtDate(cur.osm_timestamp) || "?") }),
      cur.accepted_at ? " · mises en service le " + fmtDate(cur.accepted_at) + (cur.accepted_by ? " par " + cur.accepted_by : "") : "",
    ]);
    b.appendChild(txt);
    var etiquettes = h("div", { className: "it-osm-banner-tags" });
    if (occupe()) etiquettes.appendChild(h("span", { className: "it-badge is-run" }, [h("span", { className: "it-spin" }), "Opération OSM en cours"]));
    if (cand) etiquettes.appendChild(h("span", { className: "it-badge is-warn" }, [icon("rule"), "Candidat en attente de décision"]));
    else if (up.newer) etiquettes.appendChild(h("span", { className: "it-badge is-warn" }, [icon("new_releases"), "Nouvelles données disponibles"]));
    etiquettes.appendChild(h("button", { type: "button", className: "btn btn-secondary", onclick: function () { A.switchTab("osm"); } }, "Voir"));
    b.appendChild(etiquettes);
    if (badge) {
      var n = cand && cand.counts ? cand.counts.blocking_open || 0 : 0;
      badge.hidden = !(cand || up.newer);
      badge.textContent = cand ? (n ? String(n) : "!") : "•";
      badge.title = cand ? (n ? n + " alerte(s) bloquante(s) sans décision" : "Candidat en attente") : "Nouvelles données disponibles";
    }
  }

  // ------------------------------------------------------------------ ecran

  function monter() {
    var racine = $("#it-osm");
    if (!racine || O.monte) return;
    O.monte = true;
    clear(racine);
    ["etat", "job", "cand", "rapport", "archives"].forEach(function (k) {
      racine.appendChild(h("div", { id: "it-osm-" + k, className: "it-osm-bloc" }));
    });
  }

  function afficher() {
    monter();
    charger().then(function () {
      if (candidat() && !O.rapport) chargerRapport();
      if (!O.archives) chargerArchives();
    });
  }

  function rendre() {
    rendreEtat();
    rendreJob();
    rendreCandidat();
    rendreRapport();
    rendreArchives();
  }

  function rendreEtat() {
    var z = clear($("#it-osm-etat"));
    if (O.indispo) {
      z.appendChild(h("div", { className: "it-alert is-warn" }, [icon("info"),
        h("div", { text: "Le service valhalla-admin n'expose pas encore la gestion des données OSM : version du service à déployer." })]));
      return;
    }
    if (!O.etat) { z.appendChild(h("div", { className: "it-hint", text: O.erreur || "Chargement..." })); return; }
    var cur = O.etat.current || {}, up = O.etat.upstream || {};
    var carte = h("div", { className: "it-osm-etat" });
    carte.appendChild(h("div", null, [
      h("div", { className: "it-k", text: "Données en service" }),
      h("div", { className: "it-osm-date", text: fmtDate(cur.osm_timestamp) || "?" }),
      h("div", { className: "it-sub", text: [
        cur.accepted_at ? "Mises en service le " + fmtDate(cur.accepted_at) + (cur.accepted_by ? " par " + cur.accepted_by : "") : "",
        cur.ways != null ? cur.ways + " voies, " + (cur.barriers || 0) + " barrières" : "",
      ].filter(Boolean).join(" · ") }),
    ]));
    var amont = h("div", null, [h("div", { className: "it-k", text: "OpenStreetMap" })]);
    if (up.error) amont.appendChild(h("div", { className: "it-hint it-b-warn", text: "Vérification impossible : " + up.error }));
    else if (up.newer) amont.appendChild(h("span", { className: "it-badge is-warn" }, [icon("new_releases"), "Nouvelles données disponibles (" + fmtDate(up.osm_timestamp) + ")"]));
    else amont.appendChild(h("span", { className: "it-badge is-on" }, [icon("check_circle"), "À jour"]));
    if (up.checked_at) amont.appendChild(h("div", { className: "it-sub", text: "Vérifié le " + fmtDate(up.checked_at) }));
    carte.appendChild(amont);
    var bt = h("button", { type: "button", className: "btn btn-primary", onclick: telecharger,
      disabled: occupe() || !!candidat() || null }, [icon("download"), "Télécharger les données à jour"]);
    bt.title = candidat() ? "Un candidat est déjà ouvert : l'accepter ou le rejeter d'abord" : "5 à 10 minutes (environ 370 Mo)";
    carte.appendChild(h("div", { className: "it-osm-actions" }, [bt]));
    z.appendChild(carte);
  }

  function rendreJob() {
    var z = clear($("#it-osm-job"));
    var j = job();
    if (!O.etat || !j.state || j.state === "never") return;
    var titre = j.kind === "reanalyze" ? "Analyse d'impact" : "Téléchargement des données";
    var etats = { running: ["en cours", "is-run"], success: ["terminé", "is-on"], failed: ["en échec", "is-err"], interrupted: ["interrompu", "is-err"] };
    var e = etats[j.state] || [j.state, "is-off"];
    var bloc = h("details", { className: "it-m-rulesbox it-osm-jobbox", open: j.state !== "success" || null }, [
      h("summary", null, [j.state === "running" ? h("span", { className: "it-spin" }) : null, titre + " : ",
        h("span", { className: "it-badge " + e[1], text: e[0] })]),
    ]);
    var ol = h("ol", { className: "it-steps" });
    (j.steps || []).forEach(function (s) {
      var t = s.at ? fmtDate(s.at).split(" ").pop() : "";
      ol.appendChild(h("li", null, [t ? h("span", { text: t }) : null, s.msg || ""]));
    });
    bloc.appendChild(ol);
    if (j.state === "failed") {
      bloc.appendChild(h("div", { className: "it-alert is-error" }, [icon("error"), h("div", { text: String(j.error || "erreur inconnue") })]));
    } else if (j.state === "interrupted") {
      bloc.appendChild(h("div", { className: "it-alert is-warn" }, [icon("restart_alt"),
        h("div", { text: "Opération interrompue (redémarrage du service ?)." }),
        h("button", { type: "button", className: "btn btn-secondary",
          onclick: j.kind === "reanalyze" ? reanalyser : telecharger }, "Relancer")]));
    }
    z.appendChild(bloc);
    setTimeout(function () { ol.scrollTop = ol.scrollHeight; }, 0);
  }

  function rendreCandidat() {
    var z = clear($("#it-osm-cand"));
    var c = candidat();
    if (!c) return;
    var n = c.counts || {};
    var carte = h("div", { className: "it-osm-cand" });
    carte.appendChild(h("div", { className: "it-osm-cand-tete" }, [
      h("div", null, [
        h("div", { className: "it-k", text: "Candidat" }),
        h("div", { className: "it-osm-date", text: "Données du " + (fmtDate(c.osm_timestamp) || "?") }),
        h("div", { className: "it-sub", text: "Téléchargé le " + fmtDate(c.created_at) + (c.created_by ? " par " + c.created_by : "") }),
      ]),
      h("div", { className: "it-osm-compteurs" }, ORDRE_NIVEAUX.map(function (k) {
        return h("span", { className: "it-osm-compteur", style: { borderColor: NIVEAUX[k].couleur } }, [
          h("b", { text: String(n[k] || 0) }), " " + NIVEAUX[k].libelle.toLowerCase()]);
      }).concat([h("span", { className: "it-osm-compteur" + (n.blocking_open ? " is-bloque" : "") }, [
        h("b", { text: String(n.blocking_open || 0) }), " bloquante(s) sans décision"])])),
    ]));
    var res = resumeLignes(c.summary);
    if (res.length) carte.appendChild(h("div", { className: "it-osm-resume" }, res));
    if (c.stale) {
      carte.appendChild(h("div", { className: "it-alert is-warn" }, [icon("sync_problem"),
        h("div", { text: "Une saison ou une épreuve a changé depuis l'analyse : le rapport est obsolète. Les décisions des alertes identiques seront conservées." }),
        h("button", { type: "button", className: "btn btn-secondary", onclick: reanalyser, disabled: occupe() || null }, [icon("refresh"), "Relancer l'analyse"])]));
    }
    var accepter = h("button", { type: "button", className: "btn btn-primary", onclick: ouvrirAcceptation,
      disabled: occupe() || c.stale || (n.blocking_open || 0) > 0 || null }, [icon("check"), "Accepter"]);
    if ((n.blocking_open || 0) > 0) accepter.title = "Décider d'abord des alertes bloquantes";
    else if (c.stale) accepter.title = "Relancer l'analyse d'abord";
    carte.appendChild(h("div", { className: "it-osm-actions" }, [
      accepter,
      h("button", { type: "button", className: "btn btn-secondary", style: { color: "#dc2626" }, onclick: rejeter,
        disabled: occupe() || null }, [icon("delete"), "Rejeter le candidat"]),
    ]));
    z.appendChild(carte);
  }

  function resumeLignes(s) {
    if (!s) return [];
    if (typeof s === "string") return [h("span", { text: s })];
    return Object.keys(s).filter(function (k) { return typeof s[k] === "number" || typeof s[k] === "string"; }).map(function (k) {
      return h("span", { className: "it-osm-resume-item" }, [h("b", { text: String(s[k]) }), " " + (RESUME[k] || k.replace(/_/g, " "))]);
    });
  }

  // ------------------------------------------------------------------ rapport

  function chargerRapport() {
    if (!candidat()) { O.rapport = null; rendreRapport(); return Promise.resolve(); }
    return api("GET", "/osm/candidate").then(function (res) {
      if (res.ok) O.rapport = res.body || {};
      else if (res.status === 404) O.rapport = null;
      else toast("error", "Rapport : " + errMsg(res));
      rendreRapport();
    }).catch(function () { toast("error", "Réseau indisponible"); });
  }

  function alertes() { return (O.rapport && O.rapport.alerts) || []; }

  function filtrees() {
    return alertes().filter(function (a) {
      if (O.filtreType && a.type !== O.filtreType) return false;
      if (O.filtreDoc && !(a.rules || []).some(function (r) { return r.layer + "/" + r.doc_id === O.filtreDoc; })) return false;
      return true;
    });
  }

  function rendreRapport() {
    var z = $("#it-osm-rapport");
    if (!z) return;
    if (!candidat() || !O.rapport) {
      clear(z);
      if (O.carte) { O.carte.remove(); O.carte = null; }
      if (candidat()) z.appendChild(h("div", { className: "it-hint", text: "Chargement du rapport..." }));
      return;
    }
    // La carte est conservee d'un rendu a l'autre : seule la liste est refaite
    if (!$("#it-osm-grille", z)) {
      clear(z);
      z.appendChild(h("div", { className: "it-osm-grille", id: "it-osm-grille" }, [
        h("div", { className: "it-osm-liste" }, [h("div", { id: "it-osm-filtres", className: "it-osm-filtres" }), h("div", { id: "it-osm-alertes" })]),
        h("div", { className: "it-osm-droite" }, [h("div", { id: "it-osm-carte", className: "it-map it-osm-carte" }), h("div", { id: "it-osm-detail" })]),
      ]));
    }
    rendreFiltres();
    rendreListe();
    initCarte();
    rendreDetail();
  }

  function rendreFiltres() {
    var z = clear($("#it-osm-filtres"));
    var types = {}, docs = {};
    alertes().forEach(function (a) {
      types[a.type] = (types[a.type] || 0) + 1;
      (a.rules || []).forEach(function (r) { docs[r.layer + "/" + r.doc_id] = r; });
    });
    var st = h("select", { className: "form-input" }, [h("option", { value: "", text: "Tous les types" })].concat(
      Object.keys(types).map(function (t) { return h("option", { value: t, text: libType(t) + " (" + types[t] + ")" }); })));
    st.value = O.filtreType;
    st.addEventListener("change", function () { O.filtreType = st.value; rendreListe(); });
    var sd = h("select", { className: "form-input" }, [h("option", { value: "", text: "Tous les documents" })].concat(
      Object.keys(docs).sort().map(function (k) { return h("option", { value: k, text: docLibelle(docs[k].layer, docs[k].doc_id) }); })));
    sd.value = O.filtreDoc;
    sd.addEventListener("change", function () { O.filtreDoc = sd.value; rendreListe(); });
    z.appendChild(st);
    z.appendChild(sd);
  }

  function rendreListe() {
    var z = clear($("#it-osm-alertes"));
    var liste = filtrees();
    if (!alertes().length) { z.appendChild(h("div", { className: "it-hint", text: "Aucune alerte : les nouvelles données ne touchent aucune règle." })); return; }
    ORDRE_NIVEAUX.forEach(function (niv) {
      var du = liste.filter(function (a) { return a.level === niv; });
      if (!du.length) return;
      var ouvertes = du.filter(function (a) { return !a.decision; }).length;
      var tete = h("div", { className: "it-osm-groupe-tete", style: { borderColor: NIVEAUX[niv].couleur } }, [
        icon(NIVEAUX[niv].icone), h("strong", { text: NIVEAUX[niv].libelle + " (" + du.length + ")" }),
        h("span", { className: "it-sub", text: ouvertes ? ouvertes + " sans décision" : "toutes décidées" }),
      ]);
      if (niv !== "blocking" && ouvertes) {
        tete.appendChild(h("button", { type: "button", className: "btn btn-secondary it-osm-petit", disabled: occupe() || null,
          onclick: function () { toutIgnorer(niv); } }, [icon("visibility_off"), "Tout ignorer"]));
      }
      z.appendChild(tete);
      du.forEach(function (a) {
        var ligne = h("div", { className: "it-osm-alerte" + (O.sel === a.id ? " is-sel" : "") + (a.decision ? " is-decidee" : "") }, [
          h("span", { className: "it-dot", style: { background: NIVEAUX[niv].couleur } }),
          h("span", { className: "it-osm-alerte-txt" }, [h("b", { text: libType(a.type) }), " " + (a.name || a.target || "")]),
          a.decision ? h("span", { className: "it-badge is-on", text: (DECISIONS[a.decision] || [a.decision])[0] }) : null,
        ]);
        ligne.addEventListener("click", function () { choisir(a.id); });
        z.appendChild(ligne);
      });
    });
    if (!liste.length) z.appendChild(h("div", { className: "it-hint", text: "Aucune alerte pour ces filtres." }));
  }

  function choisir(id) {
    O.sel = id;
    rendreListe();
    rendreDetail();
  }

  function alerteSel() {
    var l = alertes();
    for (var i = 0; i < l.length; i++) if (l[i].id === O.sel) return l[i];
    return null;
  }

  // ------------------------------------------------------------------ carte

  function initCarte() {
    if (O.carte || !$("#it-osm-carte")) return;
    O.carte = L.map("it-osm-carte", { zoomControl: true });
    A.baseLayers(O.carte);
    O.carte.setView([47.937, 0.214], 13);
    O.calque = L.layerGroup().addTo(O.carte);
    setTimeout(function () { O.carte.invalidateSize(); dessiner(); }, 60);
  }

  function geo(g, style, numero) {
    if (!g) return null;
    var couche = L.geoJSON(g, {
      style: function () { return style; },
      pointToLayer: function (_f, ll) {
        return L.circleMarker(ll, { radius: 8, color: "#fff", weight: 2, fillColor: style.color, fillOpacity: 1, dashArray: style.dashArray });
      },
      interactive: false,
    });
    couche.addTo(O.calque);
    if (numero != null) {
      var b = couche.getBounds();
      if (b.isValid()) {
        // Numero : constante du code, pas de donnee tierce
        L.marker(b.getCenter(), { interactive: false, icon: L.divIcon({ className: "",
          html: "<div class='it-osm-num'>" + Number(numero) + "</div>", iconSize: [20, 20], iconAnchor: [10, 10] }) }).addTo(O.calque);
      }
    }
    return couche;
  }

  function dessiner() {
    if (!O.carte) return;
    O.calque.clearLayers();
    var a = alerteSel();
    if (!a) return;
    var coul = (NIVEAUX[a.level] || NIVEAUX.info).couleur;
    var couches = [];
    if (a.before && a.before.geometry) couches.push(geo(a.before.geometry, { color: "#64748b", weight: 5, opacity: 0.85, dashArray: "6 6" }));
    if (a.after && a.after.geometry) couches.push(geo(a.after.geometry, { color: coul, weight: 6, opacity: 0.9 }));
    (a.successors || []).forEach(function (s, i) {
      couches.push(geo(s.geometry, { color: "#7c3aed", weight: 5, opacity: 0.9 }, i + 1));
    });
    var b = null;
    couches.forEach(function (c) {
      if (!c) return;
      var cb = c.getBounds();
      if (cb.isValid()) b = b ? b.extend(cb) : L.latLngBounds(cb.getSouthWest(), cb.getNorthEast());
    });
    // Recentrer seulement sur une nouvelle alerte : le rafraichissement
    // periodique ne doit pas ramener la carte que l'operateur a deplacee
    if (b && O.centree !== a.id) O.carte.fitBounds(b, { maxZoom: 18, padding: [30, 30] });
    O.centree = a.id;
  }

  // ------------------------------------------------------------------ detail

  function regleLisible(r) {
    var t = A.ruleSummary(r) || "-";
    return r && r.comment ? t + " (" + r.comment + ")" : t;
  }

  function diffTags(avant, apres) {
    avant = avant || {}; apres = apres || {};
    var cles = {};
    Object.keys(avant).concat(Object.keys(apres)).forEach(function (k) { cles[k] = 1; });
    var lignes = Object.keys(cles).sort().filter(function (k) { return avant[k] !== apres[k]; }).map(function (k) {
      return h("tr", null, [h("td", { className: "it-mono", text: k }),
        h("td", { text: avant[k] == null ? "-" : String(avant[k]) }), h("td", { text: apres[k] == null ? "-" : String(apres[k]) })]);
    });
    if (!lignes.length) return null;
    return h("table", { className: "it-table it-osm-tags" }, [
      h("thead", null, h("tr", null, [h("th", { text: "Tag" }), h("th", { text: "Avant" }), h("th", { text: "Après" })])),
      h("tbody", null, lignes)]);
  }

  function rendreDetail() {
    var z = $("#it-osm-detail");
    if (!z) return;
    clear(z);
    dessiner();
    var a = alerteSel();
    if (!a) { z.appendChild(h("div", { className: "it-hint", text: "Choisir une alerte dans la liste pour la voir sur la carte et décider." })); return; }
    var niv = NIVEAUX[a.level] || NIVEAUX.info;
    z.appendChild(h("div", { className: "it-osm-detail-tete", style: { borderColor: niv.couleur } }, [
      icon(niv.icone), h("strong", { text: libType(a.type) }), h("span", { className: "it-sub", text: niv.libelle })]));
    z.appendChild(h("div", { className: "it-sub" }, [a.name ? a.name + " · " : "",
      a.target ? h("a", { href: "https://www.openstreetmap.org/" + a.target, target: "_blank", rel: "noopener", className: "it-mono", text: a.target }) : ""]));
    z.appendChild(h("p", { className: "it-hint", text: (TYPES[a.type] || ["", ""])[1] + (a.detail ? " " + a.detail : "") }));
    z.appendChild(h("div", { className: "it-osm-legende" }, [
      h("span", null, [h("i", { className: "lg-line lg-dash" }), "avant"]),
      h("span", null, [h("i", { className: "lg-line", style: { background: niv.couleur } }), "après"]),
      (a.successors || []).length ? h("span", null, [h("i", { className: "lg-line", style: { background: "#7c3aed" } }), "morceaux / successeurs (numérotés)"]) : null]));

    if ((a.rules || []).length) {
      z.appendChild(h("div", { className: "it-k", text: "Règles concernées" }));
      z.appendChild(h("ul", { className: "it-osm-regles" }, a.rules.map(function (r) {
        return h("li", null, [h("b", { text: docLibelle(r.layer, r.doc_id) }), " : " + regleLisible(r.rule)]);
      })));
    }
    // Comparaison des tags seulement si l'objet existe avant ET apres
    var dt = a.before && a.after ? diffTags(a.before.tags, a.after.tags) : null;
    if (dt) z.appendChild(dt);

    z.appendChild(blocDecision(a));
  }

  function blocDecision(a) {
    var bloc = h("div", { className: "it-osm-decision" });
    var choix = a.decisions || [];
    if (a.decision) {
      bloc.appendChild(h("div", { className: "it-alert is-ok" }, [icon("check_circle"),
        h("div", null, ["Décision : ", h("strong", { text: (DECISIONS[a.decision] || [a.decision])[0] }),
          a.decision === "reassign" && (a.targets || []).length ? " vers " + a.targets.join(", ") : ""]),
        h("button", { type: "button", className: "btn btn-secondary", disabled: occupe() || null,
          onclick: function () { decider(a, null); } }, "Annuler la décision")]));
    }
    if (!choix.length) return bloc;
    var desactive = occupe() || null;

    // Morceaux qui recevront la regle (extend) / successeurs a cocher (reassign)
    var succ = a.successors || [];
    if (choix.indexOf("extend") >= 0 && succ.length) {
      bloc.appendChild(h("div", { className: "it-k", text: "Étendre : la règle sera copiée sur" }));
      bloc.appendChild(h("ol", { className: "it-osm-succ" }, succ.map(function (s) {
        return h("li", null, [h("span", { className: "it-mono", text: s.id }), nomTags(s.tags), s.reversed ? h("span", { className: "it-badge is-warn", text: "sens inversé" }) : null]);
      })));
    }
    var reassignBox = null;
    if (choix.indexOf("reassign") >= 0) {
      var mem = O.reassign[a.id] || { coches: (a.targets && a.targets.length ? a.targets : succ.map(function (s) { return s.id; })), libre: "" };
      O.reassign[a.id] = mem;
      reassignBox = h("div", { className: "it-osm-reassign" }, [h("div", { className: "it-k", text: "Réaffecter vers" })]);
      succ.forEach(function (s, i) {
        var cb = h("input", { type: "checkbox", value: s.id });
        cb.checked = mem.coches.indexOf(s.id) >= 0;
        cb.addEventListener("change", function () {
          mem.coches = mem.coches.filter(function (x) { return x !== s.id; });
          if (cb.checked) mem.coches.push(s.id);
        });
        reassignBox.appendChild(h("label", { className: "it-check" }, [cb, " " + (i + 1) + ". ", h("span", { className: "it-mono", text: s.id }), nomTags(s.tags),
          s.distance_m != null ? " à " + String(Math.round(s.distance_m * 10) / 10).replace(".", ",") + " m" : "",
          s.reversed ? h("span", { className: "it-badge is-warn", title: "Le sens unique de la règle sera inversé automatiquement", text: "sens inversé" }) : null]));
      });
      var libre = h("input", { className: "form-input", placeholder: "Autre id, ex. " + (String(a.target || "").split("/")[0] || "way") + "/123456 (virgules entre plusieurs)" });
      libre.value = mem.libre;
      libre.addEventListener("input", function () { mem.libre = libre.value; });
      reassignBox.appendChild(libre);
      if (succ.some(function (s) { return s.reversed; })) {
        reassignBox.appendChild(h("div", { className: "it-hint", text: "« sens inversé » : le morceau est tracé dans l'autre sens, le sens unique de la règle sera inversé automatiquement." }));
      }
      bloc.appendChild(reassignBox);
    }
    var boutons = h("div", { className: "it-osm-actions" });
    choix.forEach(function (d) {
      var lib = DECISIONS[d] || [d, "check"];
      var b = h("button", { type: "button", className: "btn " + (a.decision === d ? "btn-primary" : "btn-secondary"), disabled: desactive },
        [icon(lib[1]), lib[0]]);
      b.addEventListener("click", function () {
        if (d === "reassign") {
          var mem2 = O.reassign[a.id];
          var cibles = mem2.coches.concat(mem2.libre.split(",").map(function (x) { return x.trim(); }).filter(Boolean));
          var type = String(a.target || "").split("/")[0];
          var faux = cibles.filter(function (x) { return !new RegExp("^" + type + "/\\d+$").test(x); });
          if (!cibles.length) { toast("warning", "Cocher ou saisir au moins un objet"); return; }
          if (faux.length) { toast("warning", "Identifiant invalide (" + faux.join(", ") + ") : attendu " + type + "/<numéro>"); return; }
          decider(a, "reassign", cibles.filter(function (x, i) { return cibles.indexOf(x) === i; }));
        } else decider(a, d);
      });
      boutons.appendChild(b);
    });
    bloc.appendChild(boutons);
    return bloc;
  }

  function nomTags(tags) {
    var n = tags && (tags.name || tags.highway || tags.barrier);
    return n ? " " + n + " " : " ";
  }

  function decider(a, decision, cibles) {
    if (occupe()) return;
    var corps = { decision: decision };
    if (decision === "reassign") corps.targets = cibles;
    // Id avec ":" et "/" : encode, le relais le retransmet tel quel
    api("PUT", "/osm/candidate/alerts/" + encodeURIComponent(a.id), { body: corps }).then(function (res) {
      if (!res.ok) { toast("error", errMsg(res)); return; }
      toast("success", decision ? "Décision enregistrée" : "Décision annulée");
      Promise.all([charger(), chargerRapport()]);
    }).catch(function () { toast("error", "Réseau indisponible"); });
  }

  function toutIgnorer(niveau) {
    window.showConfirmToast("Ignorer toutes les alertes « " + NIVEAUX[niveau].libelle.toLowerCase() + " » sans décision ?",
      { okLabel: "Tout ignorer", cancelLabel: "Annuler" }).then(function (ok) {
      if (!ok) return;
      api("POST", "/osm/candidate/alerts/ignore", { body: { level: niveau } }).then(function (res) {
        if (!res.ok) { toast("error", errMsg(res)); return; }
        toast("success", ((res.body || {}).ignored || 0) + " alerte(s) ignorée(s)");
        Promise.all([charger(), chargerRapport()]);
      });
    });
  }

  // ------------------------------------------------------------------ operations

  function telecharger() {
    if (occupe()) return;
    window.showConfirmToast("Télécharger un extrait OSM à jour ? 5 à 10 minutes (environ 370 Mo). " +
      "Rien ne change dans le moteur avant l'acceptation du candidat.", { okLabel: "Télécharger", cancelLabel: "Annuler" }).then(function (ok) {
      if (!ok) return;
      api("POST", "/osm/refresh").then(function (res) {
        if (!res.ok) { toast(res.status === 409 ? "warning" : "error", errMsg(res)); return; }
        toast("info", "Téléchargement lancé");
        charger();
      }).catch(function () { toast("error", "Réseau indisponible"); });
    });
  }

  function reanalyser() {
    if (occupe()) return;
    api("POST", "/osm/candidate/reanalyze").then(function (res) {
      if (!res.ok) { toast(res.status === 409 ? "warning" : "error", errMsg(res)); return; }
      toast("info", "Analyse relancée");
      charger();
    }).catch(function () { toast("error", "Réseau indisponible"); });
  }

  function rejeter() {
    window.showConfirmToast("Rejeter le candidat ? Le rapport et toutes les décisions seront perdus ; les données en service ne changent pas.",
      { okLabel: "Rejeter", cancelLabel: "Annuler" }).then(function (ok) {
      if (!ok) return;
      api("DELETE", "/osm/candidate").then(function (res) {
        if (!res.ok) { toast("error", errMsg(res)); return; }
        toast("success", "Candidat rejeté");
        O.rapport = null; O.sel = null; O.reassign = {};
        charger();
      }).catch(function () { toast("error", "Réseau indisponible"); });
    });
  }

  // Recapitulatif reconstruit a partir des decisions (le service applique)
  function recapitulatif() {
    var lignes = { supprimees: [], reaffectees: [], etendues: [], ignorees: 0 };
    alertes().forEach(function (a) {
      var regles = a.rules || [];
      if (a.decision === "ignore") { lignes.ignorees++; return; }
      regles.forEach(function (r) {
        var qui = docLibelle(r.layer, r.doc_id) + " · " + (a.name || a.target) + " (" + regleLisible(r.rule) + ")";
        if (a.decision === "delete_rule") lignes.supprimees.push(qui);
        else if (a.decision === "reassign") lignes.reaffectees.push(qui + " → " + (a.targets || []).join(", ") +
          ((a.successors || []).some(function (s) { return s.reversed && (a.targets || []).indexOf(s.id) >= 0; }) ? " (sens inversé sur certains morceaux)" : ""));
        else if (a.decision === "extend") lignes.etendues.push(qui + " → " + (a.successors || []).map(function (s) { return s.id; }).join(", "));
      });
    });
    return lignes;
  }

  function ouvrirAcceptation() {
    var c = candidat();
    if (!c || occupe() || c.stale || (c.counts || {}).blocking_open) return;
    var ov = $("#it-osm-accept"), corps = clear($("#it-osm-accept-body")), pied = clear($("#it-osm-accept-foot"));
    var r = recapitulatif();
    corps.appendChild(h("p", { text: "Les données du " + fmtDate(c.osm_timestamp) + " remplaceront celles du " +
      fmtDate((O.etat.current || {}).osm_timestamp) + " (archivées, restaurables)." }));
    function liste(titre, l) {
      if (!l.length) return;
      corps.appendChild(h("div", { className: "it-k", text: titre + " (" + l.length + ")" }));
      corps.appendChild(h("ul", { className: "it-osm-regles" }, l.map(function (x) { return h("li", { text: x }); })));
    }
    liste("Règles supprimées", r.supprimees);
    liste("Règles réaffectées", r.reaffectees);
    liste("Règles étendues aux nouveaux morceaux", r.etendues);
    if (!r.supprimees.length && !r.reaffectees.length && !r.etendues.length) {
      corps.appendChild(h("p", { className: "it-hint", text: "Aucune règle modifiée." }));
    }
    if (r.ignorees) corps.appendChild(h("p", { className: "it-hint", text: r.ignorees + " alerte(s) ignorée(s)." }));
    corps.appendChild(h("p", { className: "it-hint", text: "Chaque document concerné reçoit une nouvelle version (auteur : vous), visible dans son historique." }));
    var cb = h("input", { type: "checkbox", id: "it-osm-load" });
    cb.checked = true;
    corps.appendChild(h("label", { className: "it-check" }, [cb, " Recharger le scénario actif"]));
    var avert = h("div", { className: "it-alert is-warn" }, [icon("warning"),
      h("div", null, [h("strong", { text: "Valhalla sera indisponible environ 30 secondes" }), " pour toutes les applications."])]);
    cb.addEventListener("change", function () { avert.hidden = !cb.checked; });
    corps.appendChild(avert);
    var go = h("button", { type: "button", className: "btn btn-primary" }, [icon("check"), "Accepter"]);
    go.addEventListener("click", function () { accepter(cb.checked, go); });
    pied.appendChild(h("button", { type: "button", className: "btn btn-secondary", onclick: function () { ov.hidden = true; } }, "Annuler"));
    pied.appendChild(go);
    ov.hidden = false;
  }

  function accepter(recharger, go) {
    go.disabled = true;
    api("POST", "/osm/candidate/accept", { body: { load: !!recharger } }).then(function (res) {
      if (!res.ok) { go.disabled = false; toast(res.status === 409 ? "warning" : "error", errMsg(res)); return; }
      var b = res.body || {};
      montrerResultat(b);
      O.rapport = null; O.sel = null; O.reassign = {};
      O.archives = null;
      charger().then(chargerArchives);
      A.apresDonnees();
      if (b.load) {
        $("#it-osm-accept").hidden = true;
        A.suivreChargement(b.load);
      }
    }).catch(function () { go.disabled = false; toast("error", "Réseau indisponible"); });
  }

  function montrerResultat(b) {
    var corps = clear($("#it-osm-accept-body")), pied = clear($("#it-osm-accept-foot"));
    corps.appendChild(h("div", { className: "it-alert is-ok" }, [icon("check_circle"), h("div", { text: "Nouvelles données acceptées. Anciennes données archivées." })]));
    var applied = b.applied || [];
    if (applied.length) {
      corps.appendChild(h("div", { className: "it-k", text: "Règles modifiées (" + applied.length + ")" }));
      var t = h("table", { className: "it-table" }, [h("thead", null, h("tr", null, ["Document", "Objet", "Avant", "Après", "Motif"].map(function (x) { return h("th", { text: x }); })))]);
      var tb = h("tbody");
      applied.forEach(function (x) {
        tb.appendChild(h("tr", null, [h("td", { text: docLibelle(x.layer, x.doc_id) }), h("td", { className: "it-mono", text: x.key }),
          h("td", { text: x.before ? regleLisible(x.before) : "-" }), h("td", { text: x.after ? regleLisible(x.after) : "supprimée" }),
          h("td", { text: x.reason || "" })]));
      });
      t.appendChild(tb);
      corps.appendChild(t);
    }
    var conflits = b.conflicts || [];
    if (conflits.length) {
      corps.appendChild(h("div", { className: "it-alert is-warn" }, [icon("warning"), h("div", null, [
        h("strong", { text: conflits.length + " conflit(s) à traiter à la main : " }),
        "la cible avait déjà une règle dans ce document, elle n'a pas été écrasée."])]));
      corps.appendChild(h("ul", { className: "it-osm-regles" }, conflits.map(function (x) {
        if (typeof x === "string") return h("li", { text: x });
        return h("li", { text: [x.layer && x.doc_id ? docLibelle(x.layer, x.doc_id) : "", x.key || x.target || "",
          x.rule ? regleLisible(x.rule) : "", x.reason || x.detail || ""].filter(Boolean).join(" · ") });
      })));
    }
    pied.appendChild(h("button", { type: "button", className: "btn btn-primary", onclick: function () { $("#it-osm-accept").hidden = true; } }, "Fermer"));
    $("#it-osm-accept").hidden = false;
  }

  // ------------------------------------------------------------------ archives

  function chargerArchives() {
    if (O.indispo) return Promise.resolve();
    return api("GET", "/osm/archives").then(function (res) {
      O.archives = res.ok ? (Array.isArray(res.body) ? res.body : (res.body && res.body.archives) || []) : [];
      rendreArchives();
    }).catch(function () { /* section vide */ });
  }

  function rendreArchives() {
    var z = $("#it-osm-archives");
    if (!z) return;
    clear(z);
    if (O.indispo || !O.archives) return;
    var bloc = h("details", { className: "it-m-rulesbox" }, [h("summary", { text: "Archives (" + O.archives.length + ")" })]);
    bloc.appendChild(h("p", { className: "it-hint", text: "Restaurer remet en service d'anciennes données OSM et recharge le scénario. " +
      "Les règles modifiées lors d'une acceptation ne sont PAS remises en l'état : passer par l'historique des saisons et épreuves." }));
    if (!O.archives.length) bloc.appendChild(h("div", { className: "it-hint", text: "Aucune archive." }));
    else {
      var t = h("table", { className: "it-table" }, [h("thead", null, h("tr", null,
        ["Données du", "Archivées le", "Par", "Voies", "Barrières", ""].map(function (x) { return h("th", { text: x }); })))]);
      var tb = h("tbody");
      O.archives.forEach(function (a) {
        var bt = h("button", { type: "button", className: "btn btn-secondary", disabled: occupe() || !!candidat() || null }, [icon("history"), "Restaurer"]);
        if (candidat()) bt.title = "Impossible tant qu'un candidat est ouvert";
        bt.addEventListener("click", function () { restaurer(a); });
        tb.appendChild(h("tr", null, [h("td", { text: fmtDate(a.osm_timestamp) }), h("td", { text: fmtDate(a.archived_at) }),
          h("td", { text: a.archived_by || "" }), h("td", { text: a.ways != null ? String(a.ways) : "" }),
          h("td", { text: a.barriers != null ? String(a.barriers) : "" }), h("td", null, bt)]));
      });
      t.appendChild(tb);
      bloc.appendChild(t);
    }
    z.appendChild(bloc);
  }

  function restaurer(a) {
    if (occupe()) return;
    window.showConfirmToast("Restaurer les données OSM du " + fmtDate(a.osm_timestamp) + " et recharger le scénario ? " +
      "Valhalla sera indisponible environ 30 secondes. Les règles modifiées lors d'acceptations ultérieures ne sont pas remises en l'état.",
      { okLabel: "Restaurer", cancelLabel: "Annuler" }).then(function (ok) {
      if (!ok) return;
      api("POST", "/osm/archives/" + encodeURIComponent(a.id) + "/restore", { body: { load: true } }).then(function (res) {
        if (!res.ok) { toast(res.status === 409 ? "warning" : "error", errMsg(res)); return; }
        toast("success", "Données restaurées");
        O.archives = null;
        charger().then(chargerArchives);
        A.apresDonnees();
        if ((res.body || {}).load) A.suivreChargement(res.body.load);
      }).catch(function () { toast("error", "Réseau indisponible"); });
    });
  }

  window.ItOsm = { afficher: afficher, occupe: occupe };

  // Bandeau du tableau de bord des l'ouverture de la page
  charger();
})();
