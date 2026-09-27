/* pmv.js -- page /pmv : remorques a panneau a message variable.
 *
 * Le navigateur ne parle JAMAIS aux remorques : il demande au serveur
 * (/api/pmv/*) de lancer une operation sur un panneau_id du registre, puis suit
 * le job (GET /api/pmv/jobs/<id>) pour afficher le deroule etape par etape.
 */
(function () {
  "use strict";

  var API = "/api/pmv";
  var ADMIN = window.__userIsAdmin === true;
  var CIRCUIT_BBOX = [[47.9295, 0.1930], [47.9720, 0.2620]];
  var POLL_MS = 500;

  var state = {
    panneaux: [],          // registre
    etats: {},             // id -> etat (resolution, affiche_deduit, dernier_envoi, sauvegarde...)
    selection: null,       // id de la remorque selectionnee
    image: null,           // {b64, nom, message_id?, source}
    messages: [], categories: [],
    edition: null,         // message de la bibliotheque ouvert dans l'editeur {id, nom}
    cible: "remorque",     // "remorque" | "groupe" (panneau d'action du poste)
    groupes: [], groupeSel: "", run: null,
    jobs: {},              // panneau_id -> job_id suivi
    jobCourant: null,      // dernier snapshot du job de la remorque selectionnee
    carte: null, calque: null, marqueurs: {}
  };

  // ------------------------------------------------------------------ outils
  function $(id) { return document.getElementById(id); }

  function csrfToken() {
    var m = document.querySelector('meta[name="csrf-token"]');
    return m ? m.getAttribute("content") : "";
  }

  function esc(s) {
    var d = document.createElement("div");
    d.textContent = s == null ? "" : String(s);
    return d.innerHTML;
  }

  function toast(type, msg, duree) {
    if (typeof window.showToast === "function") window.showToast(type, msg, duree);
  }

  /** Requete JSON ; rend {ok, status, data} sans jamais lever sur une erreur HTTP. */
  function api(methode, url, corps) {
    var opts = {
      method: methode,
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken() },
      credentials: "same-origin"
    };
    if (corps !== undefined) opts.body = JSON.stringify(corps);
    return fetch(url, opts).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) {
        return { ok: r.ok && j.ok !== false, status: r.status, data: j || {} };
      });
    }).catch(function () {
      return { ok: false, status: 0, data: { error: "reseau" } };
    });
  }

  var LIBELLES_ERREUR = {
    reseau: "Serveur Cockpit injoignable.",
    busy: "Une operation est deja en cours sur cette remorque.",
    role_admin_requis: "Action reservee aux admins.",
    role_manager_requis: "Action reservee aux managers.",
    panneau_inconnu: "Remorque inconnue.",
    panneau_inactif: "Remorque desactivee dans le registre.",
    image_invalide: "Image refusee",
    aucune_sauvegarde: "Aucune sauvegarde de l'affichage d'avant pour cette remorque.",
    plaque_invalide: "Plaque invalide.",
    plaque_existante: "Cette plaque est deja enregistree.",
    ip_invalide: "Adresse IP invalide.",
    position_invalide: "Position invalide.",
    boucle_locale_reservee_jumeau: "127.0.0.1 est reserve a une remorque marquee jumeau.",
    jumeau_sans_boucle_locale: "Un jumeau doit avoir l'IP fixe 127.0.0.1.",
    jumeau_reserve_admin: "Le jumeau de banc d'essai est reserve aux admins.",
    format_non_reconnu: "Fichier non reconnu (attendu : export de fiches PMV).",
    trop_de_fiches: "Trop de fiches dans le fichier (500 maximum).",
    nom_requis: "Le nom du message est obligatoire.",
    message_inconnu: "Ce message n'existe plus dans la bibliotheque.",
    groupe_inconnu: "Ce groupe n'existe plus.",
    groupe_vide: "Le groupe ne contient aucune remorque.",
    heure_invalide: "Date et heure invalides.",
    cible_inconnue: "Remorque ou groupe introuvable.",
    sans_sauvegarde_unitaire_seulement: "L'envoi sans sauvegarde ne se fait que remorque par remorque."
  };

  function libelleErreur(data) {
    if (!data) return "Erreur inconnue.";
    if (data.message) return data.message;
    var base = LIBELLES_ERREUR[data.error] || ("Erreur : " + (data.error || "inconnue"));
    return data.detail ? base + " : " + data.detail : base;
  }

  function plaque(p) {
    var u = String(p || "").toUpperCase();
    return /^[A-Z]{2}[0-9]{3}[A-Z]{2}$/.test(u) ? u.slice(0, 2) + "-" + u.slice(2, 5) + "-" + u.slice(5) : u;
  }

  function plaqueHTML(p) {
    return '<span class="pmv-plaque"><span>' + esc(plaque(p)) + "</span></span>";
  }

  function hhmm(iso) {
    if (!iso) return "";
    var d = new Date(iso);
    if (isNaN(d)) return "";
    return d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
  }

  function dateCourte(iso) {
    if (!iso) return "";
    var d = new Date(iso);
    if (isNaN(d)) return "";
    return d.toLocaleDateString("fr-FR", { day: "2-digit", month: "2-digit" }) + " " + hhmm(iso);
  }

  function panneau(id) {
    for (var i = 0; i < state.panneaux.length; i++) if (state.panneaux[i].id === id) return state.panneaux[i];
    return null;
  }

  function contexteEvenement() {
    var ev = window.selectedEvent, an = window.selectedYear;
    try {
      if (!ev) ev = localStorage.getItem("cockpit_event") || "";
      if (!an) an = localStorage.getItem("cockpit_year") || "";
    } catch (e) {}
    return { event: ev || null, year: an ? parseInt(an, 10) : null };
  }

  // ------------------------------------------------------------------ etat d'une remorque
  /** -> {cls, texte} pour la pastille de resolution. */
  function pastilleResolution(p, etat) {
    if (p.actif === false) return { cls: "is-unknown", texte: "Inactive" };
    var r = etat && etat.resolution;
    if (!r) return { cls: "is-unknown", texte: "Verification..." };
    if (etat.occupe) return { cls: "is-busy", texte: "Operation en cours" };
    switch (r.etat) {
      case "ok": return { cls: "is-ok", texte: r.ip + " - resolue " + hhmm(r.ts) };
      case "introuvable": return { cls: "is-ko", texte: "Introuvable - remorque eteinte ?" };
      case "dns_indisponible": return { cls: "is-warn", texte: "DNS indisponible" };
      case "ip_fixe": return { cls: "is-unknown", texte: "IP fixe " + r.ip };
      case "jumeau": return { cls: "is-jumeau", texte: "Jumeau " + r.ip };
      default: return { cls: "is-unknown", texte: "Etat inconnu" };
    }
  }

  function classeEtat(p, etat) {
    var c = pastilleResolution(p, etat).cls;
    return c === "is-ok" || c === "is-jumeau" ? "is-ok" : (c === "is-ko" ? "is-ko" : (c === "is-warn" ? "is-warn" : ""));
  }

  /** Ce qu'on sait de l'affichage : toujours une deduction (on ne lit que le nom du fichier). */
  function afficheHTML(etat) {
    var a = etat && etat.affiche_deduit;
    if (!a) return '<span class="material-symbols-outlined" style="font-size:15px;">help</span> Affichage inconnu : tester la connexion';
    if (a.type === "cockpit") {
      var img = a.png_b64 ? '<img alt="" src="data:image/png;base64,' + esc(a.png_b64) + '">' : "";
      return img + "Affiche probablement : " + esc(a.message_nom || "image envoyee par Cockpit") +
        (a.ts ? " (" + esc(dateCourte(a.ts)) + ")" : "");
    }
    return '<span class="material-symbols-outlined" style="font-size:15px;">tv</span> Message Sigma : ' +
      esc((a.noms || []).join(", ") || "liste vide") + (a.ts ? " (lu " + esc(dateCourte(a.ts)) + ")" : "");
  }

  // ------------------------------------------------------------------ chargement
  function charger(force) {
    return api("GET", API + "/panneaux").then(function (r) {
      if (!r.ok) { toast("error", libelleErreur(r.data)); return; }
      state.panneaux = r.data.panneaux || [];
      if (state.selection && !panneau(state.selection)) state.selection = null;
      rendreListe();
      rendreCarte();
      rendreRegistre();
      remplirFiltreHisto();
      rendreAction();
      chargerGroupes();
      return chargerEtats(force);
    });
  }

  function chargerEtats(force) {
    $("pmv-liste-meta").textContent = "Resolution...";
    return api("GET", API + "/panneaux/etat" + (force ? "?force=1" : "")).then(function (r) {
      if (!r.ok) { toast("error", libelleErreur(r.data)); return; }
      (r.data.etats || []).forEach(function (e) {
        state.etats[e.id] = e;
        if (e.job_en_cours && !state.jobs[e.id]) suivreJob(e.id, e.job_en_cours);
      });
      rendreListe();
      rendreCarte();
      rendreAction();
      rendreRegistre();
      $("pmv-liste-meta").textContent = "Maj " + hhmm(r.data.ts);
    });
  }

  function chargerEtat(id, force) {
    return api("GET", API + "/panneaux/" + id + "/etat" + (force ? "?force=1" : "")).then(function (r) {
      if (!r.ok) return;
      state.etats[id] = r.data.etat;
      rendreListe();
      rendreCarte();
      if (id === state.selection) rendreAction();
    });
  }

  // ------------------------------------------------------------------ liste
  function rendreListe() {
    var ul = $("pmv-liste");
    var actifs = state.panneaux.filter(function (p) { return p.actif !== false; });
    var joignables = actifs.filter(function (p) {
      var e = state.etats[p.id];
      return e && e.resolution && ["ok", "ip_fixe", "jumeau"].indexOf(e.resolution.etat) >= 0;
    });
    $("pmv-compteur-texte").textContent = joignables.length + " / " + actifs.length + " joignables";
    if (!state.panneaux.length) {
      ul.innerHTML = '<li class="pmv-empty"><span class="material-symbols-outlined">signpost</span>' +
        "Aucune remorque enregistree." + (ADMIN ? " Ajoutez-en depuis l'onglet Registre." : " Un admin doit les ajouter au registre.") + "</li>";
      return;
    }
    ul.innerHTML = state.panneaux.map(function (p) {
      var e = state.etats[p.id];
      var past = pastilleResolution(p, e);
      var cls = "pmv-item" + (p.id === state.selection ? " selected" : "") + (p.actif === false ? " inactif" : "");
      return '<li class="' + cls + '" data-id="' + esc(p.id) + '" tabindex="0">' +
        '<div class="pmv-item-head">' + plaqueHTML(p.plaque) +
        '<span class="pmv-item-nom">' + esc(p.nom || plaque(p.plaque)) + "</span>" +
        (p.jumeau ? '<span class="pmv-pill is-jumeau no-dot">Jumeau</span>' : "") + "</div>" +
        '<div class="pmv-item-loc">' + (p.localisation ? esc(p.localisation) : "Localisation non renseignee") + "</div>" +
        '<span class="pmv-pill ' + past.cls + '">' + esc(past.texte) + "</span>" +
        '<div class="pmv-item-affiche">' + afficheHTML(e) + "</div></li>";
    }).join("");
  }

  $("pmv-liste").addEventListener("click", function (ev) {
    var li = ev.target.closest(".pmv-item");
    if (li) selectionner(li.getAttribute("data-id"), true);
  });
  $("pmv-liste").addEventListener("keydown", function (ev) {
    if (ev.key !== "Enter" && ev.key !== " ") return;
    var li = ev.target.closest(".pmv-item");
    if (li) { ev.preventDefault(); selectionner(li.getAttribute("data-id"), true); }
  });

  function selectionner(id, centrer) {
    state.selection = id;
    state.jobCourant = null;
    rendreListe();
    rendreCarte();
    rendreAction();
    var p = panneau(id);
    if (centrer && p && p.lat != null && state.carte) state.carte.panTo([p.lat, p.lng]);
    if (state.jobs[id]) rafraichirJob(id);
    try { history.replaceState(null, "", "/pmv?panneau=" + encodeURIComponent(id)); } catch (e) {}
  }

  // ------------------------------------------------------------------ carte
  function fondsDeCarte(map) {
    var osm = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxNativeZoom: 19, maxZoom: 22, attribution: "&copy; OpenStreetMap"
    }).addTo(map);
    var aco = L.tileLayer("/tiles/{z}/{x}/{y}.png", { tms: true, maxZoom: 22, attribution: "ACO" });
    var ign = L.tileLayer("https://data.geopf.fr/wmts?SERVICE=WMTS&VERSION=1.0.0&REQUEST=GetTile&LAYER=ORTHOIMAGERY.ORTHOPHOTOS&STYLE=normal&TILEMATRIXSET=PM&FORMAT=image/jpeg&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}", {
      maxNativeZoom: 19, maxZoom: 22, attribution: "IGN-F/Geoplateforme"
    });
    L.control.layers({ "OSM": osm, "Satellite ACO": aco, "Satellite IGN": ign }, null, { position: "topright" }).addTo(map);
  }

  function initCarte() {
    if (state.carte || typeof L === "undefined") return;
    state.carte = L.map("pmv-carte", { zoomControl: true });
    fondsDeCarte(state.carte);
    state.carte.fitBounds(CIRCUIT_BBOX);
    state.calque = L.layerGroup().addTo(state.carte);
    setTimeout(function () { state.carte.invalidateSize(); }, 120);
  }

  function icone(p) {
    var cls = "pmv-pin " + classeEtat(p, state.etats[p.id]) + (p.id === state.selection ? " selected" : "");
    return L.divIcon({
      className: "",
      html: '<div class="' + cls + '"><span class="material-symbols-outlined">signpost</span></div>',
      iconSize: [28, 28], iconAnchor: [14, 14]
    });
  }

  function rendreCarte() {
    if (!state.carte) return;
    state.calque.clearLayers();
    state.marqueurs = {};
    var places = 0;
    state.panneaux.forEach(function (p) {
      if (p.lat == null || p.lng == null) return;
      places++;
      var m = L.marker([p.lat, p.lng], { icon: icone(p), keyboard: false }).addTo(state.calque);
      m.bindTooltip(plaque(p.plaque) + " - " + (p.nom || ""), { direction: "top", offset: [0, -14] });
      m.on("click", function () { selectionner(p.id, false); });
      state.marqueurs[p.id] = m;
    });
    $("pmv-carte-meta").textContent = places + " placee(s) sur " + state.panneaux.length;
  }

  // ------------------------------------------------------------------ image a envoyer
  // state.image = {b64, nom, message_id?, source: "fichier"|"editeur"|"bibliotheque"}
  function choisirImage(image, legende) {
    state.image = image;
    dessinerApercu(image ? "data:image/png;base64," + image.b64 : null);
    $("pmv-apercu-legende").textContent = image ? legende : "Aucune image choisie";
    if (!image || image.source !== "bibliotheque") $("pmv-source-message").value = "";
    rendreAction();
  }

  function dessinerApercu(src) {
    var c = $("pmv-apercu");
    var ctx = c.getContext("2d");
    ctx.fillStyle = "#000";
    ctx.fillRect(0, 0, 96, 64);
    if (!src) return;
    var img = new Image();
    img.onload = function () { ctx.imageSmoothingEnabled = false; ctx.drawImage(img, 0, 0); };
    img.src = src;
  }

  $("pmv-fichier").addEventListener("change", function () {
    var f = this.files && this.files[0];
    this.value = "";
    if (!f) return;
    var lecteur = new FileReader();
    lecteur.onload = function () {
      var url = String(lecteur.result);
      var img = new Image();
      img.onload = function () {
        if (img.naturalWidth !== 96 || img.naturalHeight !== 64) {
          toast("error", "L'image fait " + img.naturalWidth + " x " + img.naturalHeight + " : il faut exactement 96 x 64.");
          return;
        }
        var b64;
        if (/^data:image\/png/i.test(url)) {
          b64 = url.split(",")[1];            // PNG : octets d'origine, sans reencodage
        } else {
          var c = document.createElement("canvas");
          c.width = 96; c.height = 64;
          var ctx = c.getContext("2d");
          ctx.imageSmoothingEnabled = false;
          ctx.drawImage(img, 0, 0);
          b64 = c.toDataURL("image/png").split(",")[1];
        }
        choisirImage({ b64: b64, nom: f.name.replace(/\.[^.]+$/, ""), source: "fichier" }, "Fichier " + f.name);
      };
      img.onerror = function () { toast("error", "Image illisible."); };
      img.src = url;
    };
    lecteur.readAsDataURL(f);
  });

  // ------------------------------------------------------------------ panneau d'action
  function jobActif(id) {
    var e = state.etats[id];
    return !!state.jobs[id] || !!(e && e.occupe);
  }

  function rendreAction() {
    var groupe = state.cible === "groupe";
    $("pmv-zone-groupe").hidden = !groupe;
    $("pmv-zone-remorque").hidden = groupe;
    if (groupe) {
      $("pmv-action-vide").hidden = true;
      $("pmv-action-corps").hidden = false;
      rendreActionGroupe();
      return;
    }
    var p = state.selection ? panneau(state.selection) : null;
    $("pmv-action-vide").hidden = !!p;
    $("pmv-action-corps").hidden = !p;
    if (!p) {
      $("pmv-action-titre").textContent = "Afficher un message";
      $("pmv-action-meta").innerHTML = "";
      return;
    }
    var e = state.etats[p.id];
    $("pmv-action-titre").textContent = p.nom || plaque(p.plaque);
    var past = pastilleResolution(p, e);
    $("pmv-action-meta").innerHTML = plaqueHTML(p.plaque) + ' <span class="pmv-pill ' + past.cls + '">' + esc(past.texte) + "</span>";
    $("pmv-envoyer-libelle").textContent = "Envoyer sur " + plaque(p.plaque);

    var raison = "";
    var res = e && e.resolution;
    if (jobActif(p.id)) raison = "Operation en cours sur cette remorque";
    else if (p.actif === false) raison = "Remorque desactivee dans le registre";
    else if (res && res.etat === "introuvable") raison = "Remorque introuvable en DNS (eteinte ?)";
    else if (res && res.etat === "dns_indisponible") raison = "Resolution DNS impossible depuis le serveur";
    else if (!state.image) raison = "Choisissez d'abord une image 96 x 64";
    $("pmv-envoyer").disabled = !!raison;
    $("pmv-raison").innerHTML = raison ? '<span class="material-symbols-outlined">info</span>' + esc(raison) : "";

    var occupe = jobActif(p.id);
    $("pmv-tester").disabled = occupe || p.actif === false;
    var sauv = e && e.sauvegarde;
    $("pmv-restaurer").disabled = occupe || !sauv || p.actif === false;
    $("pmv-sauvegarde").innerHTML = sauv
      ? "Affichage d'avant sauvegarde le " + esc(dateCourte(sauv.ts)) + " : <strong>" + esc((sauv.noms || []).join(", ") || "liste vide") + "</strong>"
      : "Aucune sauvegarde : elle sera faite au premier envoi.";

    if (state.jobCourant && state.jobCourant.panneau_id === p.id) rendreJob(state.jobCourant);
    else { $("pmv-etapes").hidden = true; $("pmv-resultat").hidden = true; $("pmv-journal").hidden = true; }
  }

  function lancer(type, corps) {
    var id = state.selection;
    if (!id) return Promise.resolve();
    return api("POST", API + "/panneaux/" + id + "/" + type, corps || {}).then(function (r) {
      if (r.ok) {
        suivreJob(id, r.data.job_id);
        return;
      }
      var err = r.data.error;
      if (err === "deja_affiche") {
        return window.showConfirmToast(
          "Ce message est deja affiche sur cette remorque (d'apres l'historique). Le renvoyer quand meme ? La memoire du panneau s'use a chaque ecriture.",
          { type: "warning", okLabel: "Renvoyer", cancelLabel: "Annuler" }
        ).then(function (oui) {
          if (oui) { corps.force = true; return lancer(type, corps); }
        });
      }
      toast(err === "busy" ? "warning" : "error", libelleErreur(r.data), 5000);
      if (err === "busy") chargerEtat(id);
    });
  }

  $("pmv-envoyer").addEventListener("click", function () {
    if (!state.image) return;
    var ctx = contexteEvenement();
    var corps = { event: ctx.event, year: ctx.year };
    if (state.image.message_id) corps.message_id = state.image.message_id;
    else { corps.png_b64 = state.image.b64; corps.nom = state.image.nom; }
    lancer("envoi", corps);
  });

  $("pmv-source-message").addEventListener("change", function () {
    var m = messageParId(this.value);
    if (m) choisirImage({ b64: m.png_b64, nom: m.nom, message_id: m.id, source: "bibliotheque" }, "Bibliotheque : " + m.nom);
    else choisirImage(null);
  });

  $("pmv-source-editeur").addEventListener("click", function () {
    if (!window.PmvEditor) return;
    if (window.PmvEditor.estVide()) { toast("warning", "Le dessin de l'editeur est vide."); return; }
    imageDepuisEditeur();
  });

  function imageDepuisEditeur() {
    var nom = $("ped-nom").value.trim() || (state.edition && state.edition.nom) || "Dessin de l'editeur";
    var img = { b64: window.PmvEditor.getPNGBase64(), nom: nom, source: "editeur" };
    // Dessin intact depuis l'ouverture d'un message : c'est ce message qu'on envoie (tracabilite)
    if (state.edition && !window.PmvEditor.estModifie()) img.message_id = state.edition.id;
    choisirImage(img, "Editeur : " + nom + (img.message_id ? "" : " (non enregistre)"));
  }
  $("pmv-tester").addEventListener("click", function () { lancer("test", {}); });
  $("pmv-restaurer").addEventListener("click", function () { lancer("restaurer", {}); });

  // ------------------------------------------------------------------ suivi des jobs
  function suivreJob(panneauId, jobId) {
    state.jobs[panneauId] = jobId;
    if (panneauId === state.selection) rendreAction();
    rendreListe();
    rafraichirJob(panneauId);
  }

  function rafraichirJob(panneauId) {
    var jobId = state.jobs[panneauId];
    if (!jobId) return;
    var avecJournal = panneauId === state.selection && $("pmv-journal").open ? "?journal=1" : "";
    api("GET", API + "/jobs/" + jobId + avecJournal).then(function (r) {
      if (!r.ok) {                               // job balaye ou serveur redemarre
        delete state.jobs[panneauId];
        chargerEtat(panneauId);
        return;
      }
      var job = r.data.job;
      if (panneauId === state.selection) { state.jobCourant = job; rendreJob(job); }
      if (job.status === "done" || job.status === "error") {
        delete state.jobs[panneauId];
        if (panneauId === state.selection && !avecJournal) chargerJournal(jobId);
        notifierFin(job);
        chargerEtat(panneauId);
        if (vueActive() === "historique") chargerHistorique();
        return;
      }
      setTimeout(function () { rafraichirJob(panneauId); }, POLL_MS);
    });
  }

  function chargerJournal(jobId) {
    api("GET", API + "/jobs/" + jobId + "?journal=1").then(function (r) {
      if (r.ok && state.jobCourant && state.jobCourant.id === jobId) {
        state.jobCourant.journal = r.data.job.journal;
        $("pmv-journal-texte").textContent = (r.data.job.journal || []).join("\n");
      }
    });
  }

  function notifierFin(job) {
    if (job.panneau_id === state.selection) return;   // le resultat est deja sous les yeux
    toast(job.resultat === "ok" ? "success" : "error", (job.plaque || "") + " : " + (job.message || ""), 6000);
  }

  function iconeEtape(etat) {
    if (etat === "en_cours") return '<span class="lab-progress-ring"></span>';
    if (etat === "ok") return '<span class="material-symbols-outlined">check_circle</span>';
    if (etat === "ko") return '<span class="material-symbols-outlined">cancel</span>';
    return '<span class="pmv-point"></span>';
  }

  function rendreJob(job) {
    var ol = $("pmv-etapes");
    ol.hidden = false;
    ol.innerHTML = (job.etapes || []).map(function (e) {
      var barre = "";
      if (e.code === "image" && (e.etat === "en_cours" || e.etat === "ok")) {
        var pct = e.etat === "ok" ? 100 : (job.progress || 0);
        barre = '<div class="hsh-progress-bar"><div class="hsh-progress-fill" style="width:' + pct + '%"></div></div>';
      }
      return '<li class="pmv-step ' + esc(e.etat) + '"><div class="pmv-step-icone">' + iconeEtape(e.etat) + "</div>" +
        '<div><div class="pmv-step-libelle">' + esc(e.libelle) + "</div>" +
        (e.detail ? '<div class="pmv-step-detail">' + esc(e.detail) + "</div>" : "") + barre + "</div></li>";
    }).join("");

    var res = $("pmv-resultat");
    if (job.status === "done" || job.status === "error") {
      var ok = job.resultat === "ok";
      res.hidden = false;
      res.className = "pmv-resultat " + (ok ? "ok" : "ko");
      res.innerHTML = '<span class="material-symbols-outlined">' + (ok ? "check_circle" : "error") + "</span><div>" +
        esc(job.message || (ok ? "Termine." : "Echec.")) + "</div>";
    } else {
      res.hidden = true;
    }
    var j = $("pmv-journal");
    j.hidden = false;
    if (job.journal) $("pmv-journal-texte").textContent = job.journal.join("\n");
  }

  $("pmv-journal").addEventListener("toggle", function () {
    if (this.open && state.jobCourant) chargerJournal(state.jobCourant.id);
  });

  // ------------------------------------------------------------------ historique
  function remplirFiltreHisto() {
    var sel = $("pmv-histo-panneau");
    var val = sel.value;
    sel.innerHTML = '<option value="">Toutes</option>' + state.panneaux.map(function (p) {
      return '<option value="' + esc(p.id) + '">' + esc(plaque(p.plaque) + " - " + (p.nom || "")) + "</option>";
    }).join("");
    sel.value = val;
  }

  function chargerHistorique() {
    var q = ["limit=100"];
    if ($("pmv-histo-panneau").value) q.push("panneau_id=" + encodeURIComponent($("pmv-histo-panneau").value));
    if ($("pmv-histo-evenement").checked) {
      var c = contexteEvenement();
      if (c.event) q.push("event=" + encodeURIComponent(c.event));
      if (c.year) q.push("year=" + c.year);
    }
    var corps = $("pmv-histo-corps");
    corps.innerHTML = '<tr><td colspan="7"><span class="lab-progress-ring"></span> Chargement...</td></tr>';
    api("GET", API + "/envois?" + q.join("&")).then(function (r) {
      if (!r.ok) { corps.innerHTML = '<tr><td colspan="7">' + esc(libelleErreur(r.data)) + "</td></tr>"; return; }
      var envois = r.data.envois || [];
      $("pmv-histo-meta").textContent = envois.length + " operation(s)";
      if (!envois.length) {
        corps.innerHTML = '<tr><td colspan="7" style="color:var(--muted);">Aucun envoi pour ce filtre.</td></tr>';
        return;
      }
      corps.innerHTML = envois.map(function (e) {
        var mini = e.png_b64 ? '<img class="pmv-mini" alt="" src="data:image/png;base64,' + esc(e.png_b64) + '">'
          : '<span class="material-symbols-outlined" style="color:var(--muted);">undo</span>';
        var ok = e.resultat === "ok";
        var action = e.type === "restaurer" ? "Affichage d'avant remis" : ("Envoi" + (e.message_nom ? " : " + e.message_nom : ""));
        return "<tr><td class=\"col-shrink\">" + mini + "</td>" +
          "<td>" + plaqueHTML(e.plaque) + " " + esc(e.panneau_nom || "") + "</td>" +
          "<td>" + esc(action) + (e.sans_sauvegarde ? ' <span class="pmv-pill is-warn no-dot">sans sauvegarde</span>' : "") + "</td>" +
          "<td>" + esc(e.user_nom || e.user || "") + "</td>" +
          "<td>" + esc(dateCourte(e.fin)) + "</td>" +
          "<td class=\"col-shrink\">" + esc(String(e.duree_s != null ? e.duree_s : "").replace(".", ",")) + " s</td>" +
          '<td class="col-shrink"><span class="pmv-pill ' + (ok ? "is-ok" : "is-ko") + '" title="' + esc(e.message || "") + '">' +
          (ok ? "Reussi" : "Echec") + "</span></td></tr>";
      }).join("");
    });
  }

  $("pmv-histo-panneau").addEventListener("change", chargerHistorique);
  $("pmv-histo-evenement").addEventListener("change", chargerHistorique);

  // ------------------------------------------------------------------ registre (admin)
  function rendreRegistre() {
    var corps = $("pmv-registre-corps");
    if (!corps) return;
    if (!state.panneaux.length) {
      corps.innerHTML = '<tr><td colspan="7" style="color:var(--muted);">Registre vide. Ajoutez une remorque ou importez les fiches de l\'ancien editeur.</td></tr>';
      return;
    }
    corps.innerHTML = state.panneaux.map(function (p) {
      var past = pastilleResolution(p, state.etats[p.id]);
      var pos = p.lat != null ? '<span class="material-symbols-outlined" style="font-size:18px;color:var(--success);" title="' +
        esc(p.lat.toFixed(5) + ", " + p.lng.toFixed(5)) + '">location_on</span>'
        : '<span class="material-symbols-outlined" style="font-size:18px;color:var(--muted);" title="Aucune position">location_off</span>';
      var adr = p.ip_fixe ? esc(p.ip_fixe) : '<span style="color:var(--muted);">DNS</span>';
      return "<tr><td class=\"col-shrink\">" + plaqueHTML(p.plaque) + "</td>" +
        "<td>" + esc(p.nom || "") + (p.jumeau ? ' <span class="pmv-pill is-jumeau no-dot">Jumeau</span>' : "") + "</td>" +
        "<td>" + esc(p.localisation || "") + "</td>" +
        '<td class="col-shrink" style="text-align:center;">' + pos + "</td>" +
        '<td class="col-shrink">' + adr + "</td>" +
        '<td class="col-shrink"><span class="pmv-pill ' + past.cls + '">' + esc(past.texte) + "</span></td>" +
        '<td class="col-shrink"><div class="pmv-actions-cell">' +
        '<button class="btn-icon" type="button" data-editer="' + esc(p.id) + '" title="Modifier"><span class="material-symbols-outlined">edit</span></button>' +
        '<button class="btn-icon btn-icon-danger" type="button" data-supprimer="' + esc(p.id) + '" title="Supprimer"><span class="material-symbols-outlined">delete</span></button>' +
        "</div></td></tr>";
    }).join("");
  }

  var modale = { id: null, carte: null, marqueur: null, lat: null, lng: null };

  function ouvrirModale(p) {
    modale.id = p ? p.id : null;
    $("pmv-modal-titre").textContent = p ? "Modifier " + plaque(p.plaque) : "Nouvelle remorque";
    $("pmv-f-plaque").value = p ? plaque(p.plaque) : "";
    $("pmv-f-nom").value = p ? (p.nom || "") : "";
    $("pmv-f-loc").value = p ? (p.localisation || "") : "";
    $("pmv-f-desc").value = p ? (p.description || "") : "";
    $("pmv-f-actif").checked = p ? p.actif !== false : true;
    $("pmv-f-ip").value = p ? (p.ip_fixe || "") : "";
    $("pmv-f-jumeau").checked = p ? !!p.jumeau : false;
    $("pmv-f-erreur").textContent = "";
    $("pmv-modal-panneau").hidden = false;
    if (!modale.carte) {
      modale.carte = L.map("pmv-f-carte", { zoomControl: true });
      fondsDeCarte(modale.carte);
      modale.carte.on("click", function (ev) { placer(ev.latlng.lat, ev.latlng.lng); });
    }
    placer(p && p.lat != null ? p.lat : null, p && p.lng != null ? p.lng : null);
    setTimeout(function () {
      modale.carte.invalidateSize();
      if (modale.lat != null) modale.carte.setView([modale.lat, modale.lng], 17);
      else modale.carte.fitBounds(CIRCUIT_BBOX);
    }, 80);
    $("pmv-f-plaque").focus();
  }

  function placer(lat, lng) {
    modale.lat = lat; modale.lng = lng;
    if (lat == null) {
      if (modale.marqueur) { modale.carte.removeLayer(modale.marqueur); modale.marqueur = null; }
      $("pmv-f-coords").textContent = "Aucune position";
      return;
    }
    var ic = L.divIcon({ className: "", html: '<div class="pmv-pin is-ok"><span class="material-symbols-outlined">signpost</span></div>', iconSize: [28, 28], iconAnchor: [14, 14] });
    if (modale.marqueur) modale.marqueur.setLatLng([lat, lng]);
    else {
      modale.marqueur = L.marker([lat, lng], { icon: ic, draggable: true }).addTo(modale.carte);
      modale.marqueur.on("dragend", function () {
        var ll = modale.marqueur.getLatLng();
        modale.lat = ll.lat; modale.lng = ll.lng;
        $("pmv-f-coords").textContent = ll.lat.toFixed(6) + ", " + ll.lng.toFixed(6);
      });
    }
    $("pmv-f-coords").textContent = lat.toFixed(6) + ", " + lng.toFixed(6);
  }

  function fermerModale() { $("pmv-modal-panneau").hidden = true; }

  function enregistrer() {
    var corps = {
      plaque: $("pmv-f-plaque").value, nom: $("pmv-f-nom").value.trim(),
      localisation: $("pmv-f-loc").value.trim(), description: $("pmv-f-desc").value.trim(),
      lat: modale.lat, lng: modale.lng, actif: $("pmv-f-actif").checked,
      ip_fixe: $("pmv-f-ip").value.trim(), jumeau: $("pmv-f-jumeau").checked
    };
    var bouton = $("pmv-f-enregistrer");
    bouton.disabled = true;
    var req = modale.id ? api("PUT", API + "/panneaux/" + modale.id, corps) : api("POST", API + "/panneaux", corps);
    req.then(function (r) {
      bouton.disabled = false;
      if (!r.ok) { $("pmv-f-erreur").textContent = libelleErreur(r.data); return; }
      fermerModale();
      toast("success", modale.id ? "Remorque modifiee." : "Remorque ajoutee.");
      charger(false);
    });
  }

  if (ADMIN && $("pmv-modal-panneau")) {
    $("pmv-ajouter").addEventListener("click", function () { ouvrirModale(null); });
    $("pmv-f-enregistrer").addEventListener("click", enregistrer);
    $("pmv-f-effacer-pos").addEventListener("click", function () { placer(null, null); });
    $("pmv-f-jumeau").addEventListener("change", function () {
      if (this.checked && !$("pmv-f-ip").value.trim()) $("pmv-f-ip").value = "127.0.0.1";
    });
    $("pmv-modal-panneau").addEventListener("click", function (ev) {
      if (ev.target === this || ev.target.closest("[data-fermer]")) fermerModale();
    });
    document.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape" && !$("pmv-modal-panneau").hidden) fermerModale();
    });
    $("pmv-registre-corps").addEventListener("click", function (ev) {
      var b = ev.target.closest("button");
      if (!b) return;
      if (b.hasAttribute("data-editer")) ouvrirModale(panneau(b.getAttribute("data-editer")));
      if (b.hasAttribute("data-supprimer")) {
        var p = panneau(b.getAttribute("data-supprimer"));
        window.showConfirmToast("Supprimer " + plaque(p.plaque) + " du registre ? Sa sauvegarde d'affichage sera perdue ; l'historique est conserve.",
          { type: "warning", okLabel: "Supprimer", cancelLabel: "Annuler" }).then(function (oui) {
          if (!oui) return;
          api("DELETE", API + "/panneaux/" + p.id).then(function (r) {
            if (!r.ok) { toast("error", libelleErreur(r.data)); return; }
            if (state.selection === p.id) state.selection = null;
            toast("success", "Remorque supprimee.");
            charger(false);
          });
        });
      }
    });
    $("pmv-import-fichier").addEventListener("change", function () {
      var f = this.files && this.files[0];
      this.value = "";
      if (!f) return;
      var lecteur = new FileReader();
      lecteur.onload = function () {
        var donnees;
        try { donnees = JSON.parse(String(lecteur.result)); }
        catch (e) { toast("error", "Fichier illisible (JSON invalide)."); return; }
        api("POST", API + "/panneaux/import", donnees).then(function (r) {
          if (!r.ok) { toast("error", libelleErreur(r.data)); return; }
          toast("success", "Import : " + r.data.ajoutees + " ajoutee(s), " + r.data.mises_a_jour + " mise(s) a jour" +
            (r.data.ignorees ? ", " + r.data.ignorees + " ignoree(s)" : "") + ".", 6000);
          charger(true);
        });
      };
      lecteur.readAsText(f);
    });
  }

  // ------------------------------------------------------------------ bibliotheque de messages
  function messageParId(id) {
    for (var i = 0; i < state.messages.length; i++) if (state.messages[i].id === id) return state.messages[i];
    return null;
  }

  var rechercheTimer = null;
  function chargerBibliotheque() {
    var q = [];
    var texte = $("pmv-biblio-recherche").value.trim();
    if (texte) q.push("q=" + encodeURIComponent(texte));
    if ($("pmv-biblio-categorie").value) q.push("categorie=" + encodeURIComponent($("pmv-biblio-categorie").value));
    if ($("pmv-biblio-evenement").checked) {
      var c = contexteEvenement();
      if (c.event) q.push("event=" + encodeURIComponent(c.event));
      if (c.year) q.push("year=" + c.year);
    }
    return api("GET", API + "/messages" + (q.length ? "?" + q.join("&") : "")).then(function (r) {
      if (!r.ok) { toast("error", libelleErreur(r.data)); return; }
      state.messages = r.data.messages || [];
      state.categories = r.data.categories || [];
      rendreBibliotheque();
      remplirCategories();
      remplirSourceMessages();
    });
  }

  function remplirCategories() {
    var sel = $("pmv-biblio-categorie"), val = sel.value;
    sel.innerHTML = '<option value="">Toutes</option>' + state.categories.map(function (c) {
      return '<option value="' + esc(c) + '">' + esc(c) + "</option>";
    }).join("");
    sel.value = val;
    $("ped-categories").innerHTML = state.categories.map(function (c) { return '<option value="' + esc(c) + '">'; }).join("");
  }

  function optionsMessages(invite) {
    return '<option value="">' + esc(invite) + "</option>" + state.messages.map(function (m) {
      return '<option value="' + esc(m.id) + '">' + esc(m.nom + (m.categorie ? " (" + m.categorie + ")" : "")) + "</option>";
    }).join("");
  }

  function remplirSourceMessages() {
    [["pmv-source-message", "Choisir un message..."], ["pmv-prog-message", "Choisir un message..."]].forEach(function (d) {
      var sel = $(d[0]), val = sel.value;
      sel.innerHTML = optionsMessages(d[1]);
      sel.value = val;
    });
    majApercuProg();
  }

  function rendreBibliotheque() {
    var grille = $("pmv-biblio");
    $("pmv-biblio-meta").textContent = state.messages.length + " message(s)";
    if (!state.messages.length) {
      grille.innerHTML = '<div class="pmv-empty" style="grid-column:1/-1;"><span class="material-symbols-outlined">photo_library</span>' +
        "Aucun message pour ce filtre. Dessinez-en un dans l'editeur puis Enregistrer.</div>";
      return;
    }
    grille.innerHTML = state.messages.map(function (m) {
      var edit = state.edition && state.edition.id === m.id;
      var tags = (m.categorie ? '<span class="pmv-tag">' + esc(m.categorie) + "</span>" : "") +
        (m.etiquettes || []).map(function (t) { return '<span class="pmv-tag neutre">' + esc(t) + "</span>"; }).join("");
      return '<article class="pmv-carte-msg' + (edit ? " en-edition" : "") + '" data-id="' + esc(m.id) + '">' +
        '<div class="pmv-carte-apercu" data-action="ouvrir" title="Ouvrir dans l\'editeur"><img alt="" src="data:image/png;base64,' + esc(m.png_b64) + '"></div>' +
        '<div class="pmv-carte-info"><div class="pmv-carte-nom" title="' + esc(m.nom) + '">' + esc(m.nom) + "</div>" +
        '<div class="pmv-carte-meta">' + tags + "<span>" + esc(m.auteur_nom || "") + " - " + esc(dateCourte(m.updated_at)) + "</span></div></div>" +
        '<div class="pmv-carte-actions">' +
        '<button class="btn btn-primary pmv-btn-icon-text pmv-btn-sm" type="button" data-action="envoyer" title="Preparer l\'envoi dans l\'onglet Poste"><span class="material-symbols-outlined">send</span> Envoyer</button>' +
        '<button class="btn-icon" type="button" data-action="ouvrir" title="Ouvrir dans l\'editeur"><span class="material-symbols-outlined">edit</span></button>' +
        '<button class="btn-icon btn-icon-danger" type="button" data-action="supprimer" title="Supprimer"><span class="material-symbols-outlined">delete</span></button>' +
        "</div></article>";
    }).join("");
  }

  $("pmv-biblio").addEventListener("click", function (ev) {
    var cible = ev.target.closest("[data-action]");
    var carte = ev.target.closest(".pmv-carte-msg");
    if (!cible || !carte) return;
    var m = messageParId(carte.getAttribute("data-id"));
    if (!m) return;
    var action = cible.getAttribute("data-action");
    if (action === "envoyer") {
      afficherVue("poste");
      $("pmv-source-message").value = m.id;
      choisirImage({ b64: m.png_b64, nom: m.nom, message_id: m.id, source: "bibliotheque" }, "Bibliotheque : " + m.nom);
      if (!state.selection) toast("info", "Choisissez la remorque dans la liste ou sur la carte.");
    } else if (action === "ouvrir") {
      ouvrirMessage(m);
    } else if (action === "supprimer") {
      window.showConfirmToast("Supprimer le message \"" + m.nom + "\" de la bibliotheque ? L'historique des envois est conserve.",
        { type: "warning", okLabel: "Supprimer", cancelLabel: "Annuler" }).then(function (oui) {
        if (!oui) return;
        api("DELETE", API + "/messages/" + m.id).then(function (r) {
          if (!r.ok) { toast("error", libelleErreur(r.data)); return; }
          if (state.edition && state.edition.id === m.id) { state.edition = null; majEdition(); }
          if (state.image && state.image.message_id === m.id) choisirImage(null);
          toast("success", "Message supprime.");
          chargerBibliotheque();
        });
      });
    }
  });

  $("pmv-biblio-recherche").addEventListener("input", function () {
    clearTimeout(rechercheTimer);
    rechercheTimer = setTimeout(chargerBibliotheque, 250);
  });
  $("pmv-biblio-categorie").addEventListener("change", chargerBibliotheque);
  $("pmv-biblio-evenement").addEventListener("change", chargerBibliotheque);

  // ------------------------------------------------------------------ editeur : edition / enregistrement
  function majEdition() {
    var pill = $("ped-edition");
    var modif = window.PmvEditor && window.PmvEditor.estModifie();
    if (state.edition) {
      pill.className = "pmv-pill no-dot " + (modif ? "is-warn" : "is-jumeau");
      pill.textContent = state.edition.nom + (modif ? " - modifie" : "");
      $("ped-enregistrer-libelle").textContent = "Mettre a jour";
      $("ped-enregistrer-copie").hidden = false;
    } else {
      pill.className = "pmv-pill no-dot " + (modif ? "is-warn" : "is-unknown");
      pill.textContent = modif ? "Nouveau message - non enregistre" : "Nouveau message";
      $("ped-enregistrer-libelle").textContent = "Enregistrer";
      $("ped-enregistrer-copie").hidden = true;
    }
    document.querySelectorAll(".pmv-carte-msg").forEach(function (c) {
      c.classList.toggle("en-edition", !!state.edition && c.getAttribute("data-id") === state.edition.id);
    });
  }

  function confirmerAbandon() {
    if (!window.PmvEditor || !window.PmvEditor.estModifie() || window.PmvEditor.estVide()) return Promise.resolve(true);
    return window.showConfirmToast("Le dessin en cours n'est pas enregistre. L'abandonner ?",
      { type: "warning", okLabel: "Abandonner", cancelLabel: "Garder" });
  }

  function ouvrirMessage(m) {
    confirmerAbandon().then(function (oui) {
      if (!oui) return;
      afficherVue("messages");
      window.PmvEditor.chargerPNG(m.png_b64).then(function () {
        state.edition = { id: m.id, nom: m.nom };
        $("ped-nom").value = m.nom || "";
        $("ped-categorie").value = m.categorie || "";
        $("ped-etiquettes").value = (m.etiquettes || []).join(", ");
        majEdition();
        $("ped-root").scrollIntoView({ behavior: "smooth", block: "start" });
      }).catch(function () { toast("error", "Image du message illisible."); });
    });
  }

  function enregistrerMessage(commeNouveau) {
    var nom = $("ped-nom").value.trim();
    if (!nom) { toast("warning", "Donnez un nom au message."); $("ped-nom").focus(); return; }
    if (window.PmvEditor.estVide()) { toast("warning", "Le dessin est vide."); return; }
    var c = contexteEvenement();
    var corps = {
      nom: nom, categorie: $("ped-categorie").value.trim(), etiquettes: $("ped-etiquettes").value,
      png_b64: window.PmvEditor.getPNGBase64(), event: c.event, year: c.year
    };
    var maj = state.edition && !commeNouveau;
    var req = maj ? api("PUT", API + "/messages/" + state.edition.id, corps) : api("POST", API + "/messages", corps);
    $("ped-enregistrer").disabled = true;
    req.then(function (r) {
      $("ped-enregistrer").disabled = false;
      if (!r.ok) { toast("error", libelleErreur(r.data)); return; }
      state.edition = { id: r.data.message.id, nom: r.data.message.nom };
      window.PmvEditor.marquerPropre();
      majEdition();
      toast("success", maj ? "Message mis a jour." : "Message ajoute a la bibliotheque.");
      chargerBibliotheque();
    });
  }

  $("ped-enregistrer").addEventListener("click", function () { enregistrerMessage(false); });
  $("ped-enregistrer-copie").addEventListener("click", function () { enregistrerMessage(true); });
  $("ped-nouveau").addEventListener("click", function () {
    confirmerAbandon().then(function (oui) {
      if (!oui) return;
      window.PmvEditor.nouveau();
      state.edition = null;
      $("ped-nom").value = ""; $("ped-categorie").value = ""; $("ped-etiquettes").value = "";
      majEdition();
    });
  });
  $("ped-utiliser").addEventListener("click", function () {
    if (window.PmvEditor.estVide()) { toast("warning", "Le dessin est vide."); return; }
    imageDepuisEditeur();
    afficherVue("poste");
    if (!state.selection) toast("info", "Choisissez la remorque dans la liste ou sur la carte.");
  });
  if (window.PmvEditor) window.PmvEditor.onChange(majEdition);

  // ------------------------------------------------------------------ groupes
  function groupeParId(id) {
    for (var i = 0; i < state.groupes.length; i++) if (state.groupes[i].id === id) return state.groupes[i];
    return null;
  }

  function chargerGroupes() {
    return api("GET", API + "/groupes").then(function (r) {
      if (!r.ok) return;
      state.groupes = r.data.groupes || [];
      var sel = $("pmv-groupe-select");
      sel.innerHTML = '<option value="">Choisir un groupe...</option>' + state.groupes.map(function (g) {
        return '<option value="' + esc(g.id) + '">' + esc(g.nom + " (" + g.membres.length + ")") + "</option>";
      }).join("");
      sel.value = groupeParId(state.groupeSel) ? state.groupeSel : "";
      remplirCiblesProg();
      rendreGroupesRegistre();
      if (state.cible === "groupe") rendreAction();
    });
  }

  $("pmv-cible").addEventListener("click", function (ev) {
    var b = ev.target.closest("[data-cible]");
    if (!b) return;
    state.cible = b.getAttribute("data-cible");
    document.querySelectorAll("#pmv-cible [data-cible]").forEach(function (x) { x.classList.toggle("active", x === b); });
    rendreAction();
  });

  $("pmv-groupe-select").addEventListener("change", function () {
    state.groupeSel = this.value;
    if (!state.run || state.run.termine) state.run = null;
    rendreAction();
  });

  var LIBELLES_ECART_COURTS = { deja_affiche: "Deja affiche", busy: "Occupee", panneau_inactif: "Inactive", jumeau_reserve_admin: "Jumeau" };

  function rendreActionGroupe() {
    var g = groupeParId(state.groupeSel);
    $("pmv-action-titre").textContent = g ? "Groupe " + g.nom : "Envoi groupe";
    $("pmv-action-meta").innerHTML = g ? '<span class="pmv-pill is-unknown no-dot">' + g.membres.length + " remorque(s)</span>" : "";
    var enCours = state.run && !state.run.termine;
    var raison = "";
    if (!state.groupes.length) raison = ADMIN ? "Aucun groupe : creez-en un dans l'onglet Registre" : "Aucun groupe : un admin doit en creer";
    else if (!g) raison = "Choisissez un groupe";
    else if (enCours) raison = "Envoi groupe en cours";
    else if (!state.image) raison = "Choisissez d'abord une image 96 x 64";
    $("pmv-envoyer-groupe").disabled = !!raison;
    $("pmv-groupe-select").disabled = !!enCours;
    $("pmv-raison-groupe").innerHTML = raison ? '<span class="material-symbols-outlined">info</span>' + esc(raison) : "";
    $("pmv-envoyer-groupe-libelle").textContent = g ? "Envoyer aux " + g.membres.length + " remorques" : "Envoyer au groupe";

    var corps = $("pmv-groupe-corps");
    var run = state.run && (!g || state.run.groupe_id === g.id) ? state.run : null;
    if (run) {
      corps.innerHTML = run.entrees.map(function (e) {
        var etat, pill;
        if (e.resultat === "ecarte") { etat = e.message; pill = '<span class="pmv-pill is-warn">' + esc(LIBELLES_ECART_COURTS[e.ecarte] || "Ecartee") + "</span>"; }
        else if (e.status === "queued") { etat = "En attente de son tour"; pill = '<span class="pmv-pill is-unknown">Attente</span>'; }
        else if (e.status === "running") { etat = (e.etape || "En cours") + (e.progress ? " - " + e.progress + " %" : ""); pill = '<span class="pmv-pill is-busy"><span class="lab-progress-ring" style="width:10px;height:10px;margin:0;"></span> En cours</span>'; }
        else if (e.resultat === "ok") { etat = e.message; pill = '<span class="pmv-pill is-ok">Reussi</span>'; }
        else { etat = e.message; pill = '<span class="pmv-pill is-ko">Echec</span>'; }
        return "<tr><td>" + plaqueHTML(e.plaque) + " " + esc(e.nom || "") + '</td><td class="pmv-groupe-etat">' + esc(etat || "") + '</td><td class="col-shrink">' + pill + "</td></tr>";
      }).join("");
      var bilan = $("pmv-groupe-bilan");
      if (run.termine) {
        var b = run.bilan;
        bilan.hidden = false;
        bilan.className = "pmv-resultat " + (b.ko ? "ko" : "ok");
        bilan.innerHTML = '<span class="material-symbols-outlined">' + (b.ko ? "error" : "check_circle") + "</span><div>" +
          esc(b.ok + " reussi(s), " + b.ko + " echec(s), " + b.ecartes + " ecartee(s) sur " + b.total + "." +
            (b.ok ? " Verifiez a l'oeil." : "")) + "</div>";
      } else bilan.hidden = true;
    } else {
      $("pmv-groupe-bilan").hidden = true;
      corps.innerHTML = g ? g.membres.map(function (m) {
        var p = panneau(m.id), e = state.etats[m.id];
        var past = p ? pastilleResolution(p, e) : { cls: "is-unknown", texte: "?" };
        return "<tr><td>" + plaqueHTML(m.plaque) + " " + esc(m.nom || "") + '</td><td><span class="pmv-pill ' + past.cls + '">' +
          esc(past.texte) + '</span></td><td class="col-shrink"></td></tr>';
      }).join("") : '<tr><td colspan="3" style="color:var(--muted);">Aucun groupe choisi.</td></tr>';
    }
  }

  $("pmv-envoyer-groupe").addEventListener("click", function () {
    var g = groupeParId(state.groupeSel);
    if (!g || !state.image) return;
    var liste = g.membres.map(function (m) { return plaque(m.plaque); }).join(", ");
    window.showConfirmToast("Afficher \"" + (state.image.nom || "cette image") + "\" sur les " + g.membres.length +
      " remorques du groupe " + g.nom + " (" + liste + ") ? L'affichage de chaque panneau est remplace aussitot.",
      { type: "warning", okLabel: "Envoyer au groupe", cancelLabel: "Annuler" }).then(function (oui) {
      if (!oui) return;
      var c = contexteEvenement();
      var corps = { event: c.event, year: c.year };
      if (state.image.message_id) corps.message_id = state.image.message_id;
      else { corps.png_b64 = state.image.b64; corps.nom = state.image.nom; }
      $("pmv-envoyer-groupe").disabled = true;
      api("POST", API + "/groupes/" + g.id + "/envoi", corps).then(function (r) {
        if (!r.ok) { toast("error", libelleErreur(r.data)); rendreAction(); return; }
        state.run = r.data.run;
        state.run.groupe_id = g.id;
        rendreAction();
        suivreRun(r.data.run.id, g.id);
      });
    });
  });

  function suivreRun(rid, gid) {
    api("GET", API + "/runs/" + rid).then(function (r) {
      if (!r.ok) { state.run = null; rendreAction(); return; }
      state.run = r.data.run;
      state.run.groupe_id = gid;
      if (state.cible === "groupe") rendreAction();
      if (state.run.termine) {
        var b = state.run.bilan;
        toast(b.ko ? "warning" : "success", "Envoi groupe termine : " + b.ok + " reussi(s), " + b.ko + " echec(s), " + b.ecartes + " ecartee(s).", 6000);
        chargerEtats(false);
        return;
      }
      setTimeout(function () { suivreRun(rid, gid); }, 700);
    });
  }

  // Registre : groupes (admin)
  function rendreGroupesRegistre() {
    var corps = $("pmv-groupes-corps");
    if (!corps) return;
    if (!state.groupes.length) {
      corps.innerHTML = '<tr><td colspan="3" style="color:var(--muted);">Aucun groupe.</td></tr>';
      return;
    }
    corps.innerHTML = state.groupes.map(function (g) {
      return "<tr><td><strong>" + esc(g.nom) + "</strong></td><td>" + g.membres.map(function (m) { return plaqueHTML(m.plaque); }).join(" ") + "</td>" +
        '<td class="col-shrink"><div class="pmv-actions-cell">' +
        '<button class="btn-icon" type="button" data-g-editer="' + esc(g.id) + '" title="Modifier"><span class="material-symbols-outlined">edit</span></button>' +
        '<button class="btn-icon btn-icon-danger" type="button" data-g-supprimer="' + esc(g.id) + '" title="Supprimer"><span class="material-symbols-outlined">delete</span></button>' +
        "</div></td></tr>";
    }).join("");
  }

  var groupeEdite = null;
  function ouvrirModaleGroupe(g) {
    groupeEdite = g ? g.id : null;
    $("pmv-g-titre").textContent = g ? "Modifier le groupe" : "Nouveau groupe";
    $("pmv-g-nom").value = g ? g.nom : "";
    $("pmv-g-erreur").textContent = "";
    var membres = g ? g.membres.map(function (m) { return m.id; }) : [];
    $("pmv-g-liste").innerHTML = state.panneaux.map(function (p) {
      return '<label class="pmv-check pmv-g-item"><input type="checkbox" value="' + esc(p.id) + '"' + (membres.indexOf(p.id) >= 0 ? " checked" : "") + "> " +
        plaqueHTML(p.plaque) + " <span>" + esc(p.nom || "") + (p.localisation ? ' <span class="pmv-g-loc">' + esc(p.localisation) + "</span>" : "") + "</span>" +
        (p.jumeau ? ' <span class="pmv-pill is-jumeau no-dot">Jumeau</span>' : "") + "</label>";
    }).join("") || '<div class="pmv-help">Aucune remorque au registre.</div>';
    $("pmv-modal-groupe").hidden = false;
    $("pmv-g-nom").focus();
  }

  if (ADMIN && $("pmv-modal-groupe")) {
    $("pmv-groupe-ajouter").addEventListener("click", function () { ouvrirModaleGroupe(null); });
    $("pmv-modal-groupe").addEventListener("click", function (ev) {
      if (ev.target === this || ev.target.closest("[data-fermer]")) this.hidden = true;
    });
    $("pmv-g-enregistrer").addEventListener("click", function () {
      var ids = Array.prototype.map.call(document.querySelectorAll("#pmv-g-liste input:checked"), function (i) { return i.value; });
      var corps = { nom: $("pmv-g-nom").value.trim(), panneau_ids: ids };
      var req = groupeEdite ? api("PUT", API + "/groupes/" + groupeEdite, corps) : api("POST", API + "/groupes", corps);
      req.then(function (r) {
        if (!r.ok) {
          $("pmv-g-erreur").textContent = { nom_requis: "Le nom est obligatoire.", groupe_vide: "Cochez au moins une remorque.",
            groupe_existant: "Un groupe porte deja ce nom." }[r.data.error] || libelleErreur(r.data);
          return;
        }
        $("pmv-modal-groupe").hidden = true;
        toast("success", groupeEdite ? "Groupe modifie." : "Groupe cree.");
        chargerGroupes();
      });
    });
    $("pmv-groupes-corps").addEventListener("click", function (ev) {
      var b = ev.target.closest("button");
      if (!b) return;
      if (b.hasAttribute("data-g-editer")) ouvrirModaleGroupe(groupeParId(b.getAttribute("data-g-editer")));
      if (b.hasAttribute("data-g-supprimer")) {
        var g = groupeParId(b.getAttribute("data-g-supprimer"));
        window.showConfirmToast("Supprimer le groupe " + g.nom + " ? Ses programmations en attente seront annulees.",
          { type: "warning", okLabel: "Supprimer", cancelLabel: "Annuler" }).then(function (oui) {
          if (!oui) return;
          api("DELETE", API + "/groupes/" + g.id).then(function (r) {
            if (!r.ok) { toast("error", libelleErreur(r.data)); return; }
            toast("success", "Groupe supprime" + (r.data.programmations_annulees ? " (" + r.data.programmations_annulees + " programmation(s) annulee(s))" : "") + ".");
            chargerGroupes();
          });
        });
      }
    });
  }

  // ------------------------------------------------------------------ programmation
  var STATUTS_PROG = {
    en_attente: ["is-unknown", "Programmee"], en_cours: ["is-busy", "En cours"], terminee: ["is-ok", "Envoyee"],
    partielle: ["is-warn", "Partielle"], echec: ["is-ko", "Echec"], annulee: ["is-unknown", "Annulee"], manquee: ["is-ko", "Manquee"]
  };

  function remplirCiblesProg() {
    var sel = $("pmv-prog-cible"), val = sel.value;
    var remorques = state.panneaux.filter(function (p) { return p.actif !== false; });
    sel.innerHTML = '<option value="">Choisir une remorque ou un groupe...</option>' +
      (state.groupes.length ? '<optgroup label="Groupes">' + state.groupes.map(function (g) {
        return '<option value="groupe:' + esc(g.id) + '">' + esc(g.nom + " (" + g.membres.length + " remorques)") + "</option>";
      }).join("") + "</optgroup>" : "") +
      '<optgroup label="Remorques">' + remorques.map(function (p) {
        return '<option value="panneau:' + esc(p.id) + '">' + esc(plaque(p.plaque) + " - " + (p.nom || "")) + "</option>";
      }).join("") + "</optgroup>";
    sel.value = val;
  }

  function majApercuProg() {
    var m = messageParId($("pmv-prog-message").value), img = $("pmv-prog-apercu");
    img.hidden = !m;
    if (m) img.src = "data:image/png;base64," + m.png_b64;
  }
  $("pmv-prog-message").addEventListener("change", majApercuProg);

  function valeurDateLocale(d) {
    function z(n) { return String(n).padStart(2, "0"); }
    return d.getFullYear() + "-" + z(d.getMonth() + 1) + "-" + z(d.getDate()) + "T" + z(d.getHours()) + ":" + z(d.getMinutes());
  }

  function chargerProgrammations() {
    if (!$("pmv-prog-at").value) {
      var d = new Date(Date.now() + 30 * 60000);
      d.setMinutes(Math.ceil(d.getMinutes() / 5) * 5, 0, 0);
      $("pmv-prog-at").value = valeurDateLocale(d);
    }
    return api("GET", API + "/programmations").then(function (r) {
      if (!r.ok) { toast("error", libelleErreur(r.data)); return; }
      $("pmv-prog-ecart").textContent = r.data.ecart_min;
      $("pmv-prog-meta").textContent = r.data.a_venir.length + " programmation(s)";
      function ligne(p, passee) {
        var st = STATUTS_PROG[p.statut] || ["is-unknown", p.statut];
        var mini = p.png_b64 ? '<img class="pmv-mini" alt="" src="data:image/png;base64,' + esc(p.png_b64) + '">' : "";
        var detail = "";
        if (passee) {
          if (p.bilan) detail = p.bilan.ok + " reussi(s), " + p.bilan.ko + " echec(s), " + p.bilan.ecartes + " ecartee(s)";
          if (p.motif) detail = (detail ? detail + " - " : "") + p.motif;
        }
        return '<tr><td class="col-shrink">' + mini + "</td><td><strong>" + esc(dateCourte(p.at)) + "</strong><br><span class=\"pmv-g-loc\">" + esc(p.message_nom || "") + "</span></td>" +
          "<td>" + esc(p.cible_nom || "") + "</td><td>" + esc(p.cree_par_nom || "") + "</td>" +
          '<td class="col-shrink"><span class="pmv-pill ' + st[0] + '">' + esc(st[1]) + "</span></td>" +
          (passee ? "<td>" + esc(detail) + "</td>"
            : '<td class="col-shrink">' + (p.statut === "en_attente" ? '<button class="btn-icon btn-icon-danger" type="button" data-annuler="' + esc(p.id) + '" title="Annuler"><span class="material-symbols-outlined">event_busy</span></button>' : "") + "</td>") +
          "</tr>";
      }
      $("pmv-prog-a-venir").innerHTML = r.data.a_venir.length ? r.data.a_venir.map(function (p) { return ligne(p, false); }).join("")
        : '<tr><td colspan="6" style="color:var(--muted);">Aucun envoi programme.</td></tr>';
      $("pmv-prog-passees").innerHTML = r.data.passees.length ? r.data.passees.map(function (p) { return ligne(p, true); }).join("")
        : '<tr><td colspan="6" style="color:var(--muted);">Aucune programmation passee.</td></tr>';
    });
  }

  $("pmv-prog-creer").addEventListener("click", function () {
    var cible = $("pmv-prog-cible").value, msg = $("pmv-prog-message").value, at = $("pmv-prog-at").value;
    if (!cible) { toast("warning", "Choisissez une remorque ou un groupe."); return; }
    if (!msg) { toast("warning", "Choisissez un message de la bibliotheque."); return; }
    if (!at) { toast("warning", "Indiquez la date et l'heure."); return; }
    var c = contexteEvenement(), morceaux = cible.split(":");
    api("POST", API + "/programmations", { cible_type: morceaux[0], cible_id: morceaux[1], message_id: msg, at: at, event: c.event, year: c.year })
      .then(function (r) {
        if (!r.ok) { toast("error", libelleErreur(r.data), 7000); return; }
        toast("success", "Envoi programme le " + at.replace("T", " a ") + ".");
        chargerProgrammations();
      });
  });

  $("pmv-prog-a-venir").addEventListener("click", function (ev) {
    var b = ev.target.closest("[data-annuler]");
    if (!b) return;
    window.showConfirmToast("Annuler cette programmation ?", { type: "warning", okLabel: "Annuler l'envoi", cancelLabel: "Garder" }).then(function (oui) {
      if (!oui) return;
      api("DELETE", API + "/programmations/" + b.getAttribute("data-annuler")).then(function (r) {
        if (!r.ok) { toast("error", libelleErreur(r.data)); return; }
        toast("success", "Programmation annulee.");
        chargerProgrammations();
      });
    });
  });

  // ------------------------------------------------------------------ onglets
  function vueActive() {
    var b = document.querySelector("#pmv-onglets .lab-tab.active");
    return b ? b.getAttribute("data-vue") : "poste";
  }

  function afficherVue(nom) {
    document.querySelectorAll("#pmv-onglets .lab-tab").forEach(function (b) {
      b.classList.toggle("active", b.getAttribute("data-vue") === nom);
    });
    document.querySelectorAll(".pmv-view").forEach(function (v) {
      v.classList.toggle("active", v.getAttribute("data-vue") === nom);
    });
    if (nom === "poste" && state.carte) setTimeout(function () { state.carte.invalidateSize(); }, 60);
    if (nom === "historique") chargerHistorique();
    if (nom === "programmation") { remplirCiblesProg(); chargerProgrammations(); }
    if (nom === "messages") {
      if (window.PmvEditor) window.PmvEditor.afficher();
      chargerBibliotheque();
    }
    try { localStorage.setItem("pmv_vue", nom); } catch (e) {}
  }

  $("pmv-onglets").addEventListener("click", function (ev) {
    var b = ev.target.closest(".lab-tab");
    if (b) afficherVue(b.getAttribute("data-vue"));
  });

  $("pmv-actualiser").addEventListener("click", function () { charger(true); });

  // ------------------------------------------------------------------ demarrage
  initCarte();
  dessinerApercu(null);
  var demande = new URLSearchParams(location.search).get("panneau");
  var vueMemo = null;
  try { vueMemo = localStorage.getItem("pmv_vue"); } catch (e) {}
  if (!demande && vueMemo && document.querySelector('#pmv-onglets .lab-tab[data-vue="' + vueMemo + '"]')) afficherVue(vueMemo);
  charger(false).then(function () { if (demande && panneau(demande)) selectionner(demande, true); });
  if (vueActive() !== "messages") chargerBibliotheque();
  window.addEventListener("beforeunload", function (e) {
    if (window.PmvEditor && window.PmvEditor.estModifie() && !window.PmvEditor.estVide()) { e.preventDefault(); e.returnValue = ""; }
  });
  // Resolution rafraichie toutes les 2 minutes tant que la page est ouverte (IP 4G changeante)
  setInterval(function () { if (!document.hidden) chargerEtats(false); }, 120000);
})();
