/* Administration du module Formation (/admin/formation).
   Onglets Candidats, Campagne, Resultats, Reglages. Toute ecriture envoie
   X-CSRFToken (balise meta csrf-token). Aucun innerHTML sur une donnee : les
   textes passent par el() / textContent. */
(function () {
  "use strict";

  var API = "/api/formation/admin";
  var EPREUVES = ["radio", "appel", "mc", "patrouilles", "acces", "logique", "situations"];
  var NOMS = {radio: "Radio", appel: "Appel 38 17", mc: "Main courante", patrouilles: "Patrouilles",
              acces: "Accès", logique: "Logique", situations: "Mises en situation"};
  var COURTS = {radio: "Radio", appel: "3817", mc: "MC", patrouilles: "Patr.", acces: "Accès",
                logique: "Log.", situations: "Sit."};
  var STATUTS = {non_commence: "Non commencé", en_cours: "En cours", terminee: "Terminé",
                 interrompue: "Interrompu", reinitialisee: "Réinitialisée"};
  var ETATS_EP = {a_faire: "À faire", en_cours: "En cours", terminee: "Terminée", abandonnee: "Abandonnée"};
  var ERREURS = {
    reply_to_vide: "Renseignez le Reply-To dans les réglages avant d'envoyer.",
    contact_rgpd_vide: "Renseignez le contact RGPD dans les réglages avant d'envoyer.",
    adresse_invalide: "Adresse e-mail invalide.",
    smtp: "Le relais SMTP a refusé l'envoi.",
    confirmation_requise: "Confirmation manquante.",
    cible_inconnue: "Cible inconnue.",
    session_expiree: "Session expirée, rechargez la page.",
    gabarit_invalide: "Objet et corps obligatoires.",
    motif_requis: "Le motif est obligatoire.",
    aucune_tentative: "Aucune tentative à réinitialiser.",
    fichier_illisible: "Fichier illisible : un export .xlsx de Forms est attendu.",
    fichier_manquant: "Choisissez un fichier.",
    champ_inconnu: "Champ inconnu.",
    ouverture_invalide: "Date d'ouverture invalide.",
    fermeture_invalide: "Date de fermeture invalide.",
    fermeture_avant_ouverture: "La fermeture doit être postérieure à l'ouverture.",
    coef_invalide: "Le coefficient doit être compris entre 1 et 3.",
    ponderations_invalides: "Pondérations : nombres positifs ou nuls, au moins une non nulle.",
    reply_to_invalide: "Reply-To : adresse invalide.",
    contact_rgpd_invalide: "Contact RGPD : adresse invalide.",
    expediteur_invalide: "Nom d'expéditeur invalide (100 caractères, une ligne).",
    debit_invalide: "Délai entre deux e-mails : 0,5 s minimum, 60 s maximum.",
    csrf: "Session expirée : rechargez la page."
  };

  // --- Outils -----------------------------------------------------------------

  function $(id) { return document.getElementById(id); }

  function el(tag, attrs, enfants) {
    var n = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (k) {
        var v = attrs[k];
        if (v === null || v === undefined || v === false) return;
        if (k === "class") n.className = v;
        else if (k === "text") n.textContent = v;
        else if (k.slice(0, 2) === "on") n.addEventListener(k.slice(2), v);
        else n.setAttribute(k, v === true ? "" : v);
      });
    }
    (enfants || []).forEach(function (c) {
      if (c === null || c === undefined || c === false) return;
      n.appendChild(typeof c === "string" || typeof c === "number" ? document.createTextNode(String(c)) : c);
    });
    return n;
  }

  function vider(n) { while (n.firstChild) n.removeChild(n.firstChild); return n; }

  function icone(nom) { return el("span", {"class": "material-symbols-outlined", text: nom}); }

  function csrf() {
    var m = document.querySelector('meta[name="csrf-token"]');
    return m ? m.getAttribute("content") : "";
  }

  function api(methode, chemin, corps, brut) {
    var opts = {method: methode, credentials: "same-origin", headers: {"Accept": "application/json"}};
    if (methode !== "GET") opts.headers["X-CSRFToken"] = csrf();
    if (brut) opts.body = brut;
    else if (corps !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(corps);
    }
    return fetch(API + chemin, opts).then(function (r) {
      var json = (r.headers.get("Content-Type") || "").indexOf("application/json") >= 0;
      if (r.redirected || (r.ok && !json)) {
        return {ok: false, error: "session_expiree", _status: r.status};
      }
      return r.json().catch(function () { return {ok: false, error: "http_" + r.status}; })
        .then(function (d) { d = d || {}; d._status = r.status; return d; });
    });
  }

  function message(d) {
    var code = d && (d.error || d.code);
    return ERREURS[code] || (d && d.detail) || ("Erreur" + (code ? " : " + code : ""));
  }

  function toast(type, texte) {
    if (typeof showToast === "function") showToast(type, texte);
  }

  function fmtDate(iso) {
    if (!iso) return "";
    var d = new Date(iso);
    if (isNaN(d)) return "";
    return d.toLocaleString("fr-FR", {timeZone: "Europe/Paris", day: "2-digit", month: "2-digit",
                                      hour: "2-digit", minute: "2-digit"});
  }

  function fmtDuree(s) {
    if (s === null || s === undefined) return "";
    var m = Math.floor(s / 60), r = Math.round(s % 60);
    return m + " min " + (r < 10 ? "0" : "") + r;
  }

  function fmtNum(v) { return v === null || v === undefined ? "" : String(v).replace(".", ","); }

  function pill(statut) {
    return el("span", {"class": "fa-pill fa-st-" + statut, text: STATUTS[statut] || statut});
  }

  // --- Fenetre modale -----------------------------------------------------------

  function modale(titre, contenu, libelleOk, classeOk) {
    return new Promise(function (resolve) {
      $("fa-m-titre").textContent = titre;
      var corps = vider($("fa-m-corps"));
      corps.appendChild(contenu);
      var ok = $("fa-m-ok"), annuler = $("fa-m-annuler");
      ok.textContent = libelleOk || "Confirmer";
      ok.className = "fa-btn " + (classeOk || "fa-primary");
      $("fa-modal").hidden = false;
      var focus = contenu.querySelector("textarea,input");
      (focus || ok).focus();
      function fin(v) {
        $("fa-modal").hidden = true;
        ok.removeEventListener("click", surOk);
        annuler.removeEventListener("click", surAnnuler);
        document.removeEventListener("keydown", surTouche);
        resolve(v);
      }
      function surOk() { fin(true); }
      function surAnnuler() { fin(false); }
      function surTouche(e) { if (e.key === "Escape") fin(false); }
      ok.addEventListener("click", surOk);
      annuler.addEventListener("click", surAnnuler);
      document.addEventListener("keydown", surTouche);
    });
  }

  // --- Onglets ----------------------------------------------------------------------

  var ongletCourant = "candidats";

  function ouvrirOnglet(nom) {
    ongletCourant = nom;
    document.querySelectorAll(".fa-tab").forEach(function (b) {
      var actif = b.getAttribute("data-tab") === nom;
      b.classList.toggle("is-active", actif);
      b.setAttribute("aria-selected", actif ? "true" : "false");
    });
    document.querySelectorAll(".fa-panel").forEach(function (p) {
      p.hidden = p.getAttribute("data-panel") !== nom;
    });
    try { history.replaceState(null, "", "#" + nom); } catch (e) { /* sans effet */ }
    if (nom === "candidats") chargerCandidats();
    if (nom === "campagne") { chargerGabarit(); chargerEtat(); }
    if (nom === "resultats") chargerStats();
    if (nom === "reglages") chargerReglages();
  }

  // --- Bandeau de purge ----------------------------------------------------------------

  function chargerBandeau() {
    api("GET", "/etat").then(function (d) {
      var b = $("fa-purge");
      if (d.ok && d.purge && d.purge.rappel) {
        vider(b).appendChild(icone("delete_forever"));
        b.appendChild(el("span", {text: "Rappel : toutes les données du module (candidats, tentatives, envois, " +
          "journal) doivent être supprimées le 30/01/2027, après la sélection. La purge est manuelle."}));
        b.hidden = false;
      } else {
        b.hidden = true;
      }
    });
  }

  // --- Candidats ---------------------------------------------------------------------------

  var lignes = [];
  var tri = {cle: "nom", sens: 1};
  var ouvert = null;
  var COLONNES = [
    {cle: "forms_id", titre: "Id Forms", num: true},
    {cle: "nom", titre: "Nom"},
    {cle: "prenom", titre: "Prénom"},
    {cle: "email", titre: "E-mail"},
    {cle: "statut", titre: "Statut"},
    {cle: "debut", titre: "Début"},
    {cle: "duree_s", titre: "Durée", num: true}
  ].concat(EPREUVES.map(function (e) { return {cle: "s_" + e, titre: COURTS[e], num: true, ep: e}; }))
   .concat([
    {cle: "global", titre: "Global", num: true},
    {cle: "amenagement", titre: "Amén."},
    {cle: "reprises", titre: "Repr.", num: true},
    {cle: "drapeaux", titre: "Drapeaux"},
    {cle: "dernier_envoi", titre: "Dernier envoi"}
  ]);

  function valeurTri(l, cle) {
    if (cle.slice(0, 2) === "s_") return l.scores[cle.slice(2)];
    if (cle === "drapeaux") return l.drapeaux.length;
    if (cle === "dernier_envoi") return l.dernier_envoi ? l.dernier_envoi.date : null;
    if (cle === "statut") return Object.keys(STATUTS).indexOf(l.statut);
    return l[cle];
  }

  function trier() {
    var c = tri.cle, s = tri.sens;
    lignes.sort(function (a, b) {
      var x = valeurTri(a, c), y = valeurTri(b, c);
      var vx = x === null || x === undefined || x === "", vy = y === null || y === undefined || y === "";
      if (vx && vy) return 0;
      if (vx) return 1;
      if (vy) return -1;
      if (typeof x === "string" && typeof y === "string") return s * x.localeCompare(y, "fr");
      return s * (x < y ? -1 : x > y ? 1 : 0);
    });
  }

  function entete() {
    var tr = vider($("fa-thead"));
    COLONNES.forEach(function (c) {
      var th = el("th", {"class": (c.num ? "fa-num " : "") + (tri.cle === c.cle ? "is-sorted" : ""),
                         title: c.ep ? NOMS[c.ep] : null, scope: "col",
                         "aria-sort": tri.cle === c.cle ? (tri.sens > 0 ? "ascending" : "descending") : null},
                  [c.titre + (tri.cle === c.cle ? (tri.sens > 0 ? " ▲" : " ▼") : "")]);
      th.addEventListener("click", function () {
        tri = {cle: c.cle, sens: tri.cle === c.cle ? -tri.sens : 1};
        trier(); entete(); corpsTableau();
      });
      tr.appendChild(th);
    });
  }

  function cellule(l, c) {
    if (c.ep) return el("td", {"class": "fa-num", text: fmtNum(l.scores[c.ep])});
    switch (c.cle) {
      case "statut": return el("td", null, [pill(l.statut), l.fictif ? el("span", {"class": "fa-tag", text: "fictif"}) : null]);
      case "debut": return el("td", {text: fmtDate(l.debut)});
      case "duree_s": return el("td", {"class": "fa-num", text: fmtDuree(l.duree_s)});
      case "global": return el("td", {"class": "fa-num", text: fmtNum(l.global)});
      case "amenagement": return el("td", {text: l.amenagement ? "oui" + (l.coef && l.coef !== 1 ? " (x" + fmtNum(l.coef) + ")" : "") : ""});
      case "reprises": return el("td", {"class": "fa-num", text: l.reprises ? String(l.reprises) : ""});
      case "drapeaux":
        return el("td", null, l.drapeaux.map(function (d) { return el("span", {"class": "fa-flag", text: d}); }));
      case "dernier_envoi":
        var e = l.dernier_envoi;
        return el("td", {text: e ? e.type + " " + (e.statut || "") + " " + fmtDate(e.date) : ""});
      default:
        return el("td", {"class": c.num ? "fa-num" : null, text: l[c.cle] === null || l[c.cle] === undefined ? "" : String(l[c.cle])});
    }
  }

  function corpsTableau() {
    var tb = vider($("fa-tbody"));
    if (!lignes.length) {
      tb.appendChild(el("tr", null, [el("td", {"class": "fa-vide", colspan: COLONNES.length, text: "Aucun candidat."})]));
    }
    lignes.forEach(function (l) {
      var tr = el("tr", {"class": ouvert === l.id ? "is-open" : null, tabindex: "0"});
      COLONNES.forEach(function (c) { tr.appendChild(cellule(l, c)); });
      tr.addEventListener("click", function () { ouvrirDetail(l.id); });
      tr.addEventListener("keydown", function (e) { if (e.key === "Enter") ouvrirDetail(l.id); });
      tb.appendChild(tr);
    });
    $("fa-compte").textContent = lignes.length + " candidat" + (lignes.length > 1 ? "s" : "");
  }

  var requeteCandidats = 0;
  function chargerCandidats() {
    var p = new URLSearchParams();
    if ($("fa-f-statut").value) p.set("statut", $("fa-f-statut").value);
    if ($("fa-f-q").value.trim()) p.set("q", $("fa-f-q").value.trim());
    if ($("fa-f-fictifs").checked) p.set("fictifs", "1");
    var n = ++requeteCandidats;
    return api("GET", "/candidats?" + p.toString()).then(function (d) {
      if (n !== requeteCandidats) return;
      if (!d.ok) { toast("error", message(d)); return; }
      lignes = d.candidats;
      trier(); entete(); corpsTableau();
    });
  }

  // --- Import ------------------------------------------------------------------------------

  function listeRapport(titre, items, rendu) {
    if (!items || !items.length) return null;
    return el("div", null, [el("b", {text: titre + " (" + items.length + ")"}),
      el("ul", null, items.map(function (x) { return el("li", {text: rendu(x)}); }))]);
  }

  function afficherRapport(r) {
    var z = vider($("fa-import-rapport"));
    z.appendChild(el("div", {"class": "fa-rapport"}, [
      el("div", {"class": "fa-kpis"}, [
        kpi(r.lignes, "lignes lues"), kpi(r.crees, "créés"), kpi(r.mis_a_jour, "mis à jour"),
        kpi(r.inchanges, "inchangés"), kpi(r.doublons.length, "doublons"),
        kpi(r.inexploitables.length, "inexploitables", r.inexploitables.length > 0)]),
      listeRapport("Doublons d'e-mail (réponse la plus récente gardée)", r.doublons, function (d) {
        return d.email + " : réponses " + d.ids_forms.join(", ") + ", gardée " + d.garde; }),
      listeRapport("Lignes inexploitables", r.inexploitables, function (d) {
        return "Id Forms " + d.forms_id + " : " + d.motif; }),
      listeRapport("Candidats déjà connus absents de ce fichier (non supprimés)", r.absents_du_fichier, function (e) { return e; })
    ]));
  }

  function importer(ev) {
    ev.preventDefault();
    var f = $("fa-import-fichier").files[0];
    if (!f) { toast("warning", ERREURS.fichier_manquant); return; }
    var fd = new FormData();
    fd.append("fichier", f);
    var bouton = ev.target.querySelector("button");
    bouton.disabled = true;
    api("POST", "/import", undefined, fd).then(function (d) {
      bouton.disabled = false;
      if (!d.ok) { toast("error", message(d)); return; }
      afficherRapport(d.rapport);
      toast("success", "Import terminé.");
      chargerCandidats();
    });
  }

  // --- Detail ------------------------------------------------------------------------------

  function dl(paires) {
    var d = el("dl", {"class": "fa-dl"});
    paires.forEach(function (p) {
      if (p[1] === null || p[1] === undefined || p[1] === "") return;
      d.appendChild(el("dt", {text: p[0]}));
      d.appendChild(el("dd", null, [p[1]]));
    });
    return d;
  }

  // Libelles de l'enonce : {id: texte} (questions, choix, messages, incidents, badges, fiches)
  function libelles(obj, acc) {
    acc = acc || {};
    if (Array.isArray(obj)) obj.forEach(function (x) { libelles(x, acc); });
    else if (obj && typeof obj === "object") {
      if (typeof obj.id === "string") {
        var t = obj.texte || obj.appelant || obj.txt || obj.name || (obj.num ? obj.col + " " + obj.num + " (jusqu'à " + obj.until + ")" : "");
        if (t) acc[obj.id] = t;
      }
      Object.keys(obj).forEach(function (k) { if (typeof obj[k] === "object") libelles(obj[k], acc); });
    }
    return acc;
  }

  function verdict(ep, a, corr, enonceLib) {
    var c = corr || {};
    if (ep === "appel" || ep === "situations") {
      var ch = c[a.choix];
      return ch && ch.points !== undefined ? ch.points + " pt" + (ch.points > 1 ? "s" : "") : "";
    }
    if (ep === "logique") {
      var q = c[a.question];
      if (!q || !q.a) return "";
      return a.choix === q.a ? el("span", {"class": "fa-ok", text: "juste"})
        : el("span", {"class": "fa-ko", text: "faux (attendu : " + (enonceLib[q.a] || q.a) + ")"});
    }
    if (ep === "mc") {
      var f = c[a.question];
      if (!f || !f.ans) return "";
      var bons = Object.keys(f.ans).filter(function (k) {
        return typeof a[k] === "string" && a[k].trim() === f.ans[k]; });
      return bons.length + "/4 (attendu : " + Object.keys(f.ans).map(function (k) { return f.ans[k]; }).join(", ") + ")";
    }
    if (ep === "acces") {
      var b = c[a.badge];
      if (!b || b.ok === undefined || a.type !== "decision") return "";
      return Number(a.decision) === Number(b.ok) ? el("span", {"class": "fa-ok", text: "juste"})
        : el("span", {"class": "fa-ko", text: "faux"});
    }
    return "";
  }

  var CHAMPS_CACHES = {aid: 1, t_ms: 1, recu_ms: 1, type: 1};

  function tableActions(ep, actions, corr, lib) {
    if (!actions || !actions.length) return el("p", {"class": "fa-muted", text: "Aucune action reçue."});
    var t = el("table", {"class": "fa-mini"}, [el("thead", null, [el("tr", null, [
      el("th", {text: "t (s)"}), el("th", {text: "reçu (s)"}), el("th", {text: "type"}),
      el("th", {text: "contenu"}), el("th", {text: "correction"})])])]);
    var tb = el("tbody");
    actions.forEach(function (a) {
      var contenu = Object.keys(a).filter(function (k) { return !CHAMPS_CACHES[k]; }).map(function (k) {
        var v = a[k];
        var s = typeof v === "object" ? JSON.stringify(v) : String(v);
        return k + " : " + (typeof v === "string" && lib[v] ? lib[v] + " [" + v + "]" : s);
      }).join(" · ");
      var sec = function (ms) { return typeof ms === "number" ? (ms / 1000).toFixed(1).replace(".", ",") : ""; };
      tb.appendChild(el("tr", null, [el("td", {text: sec(a.t_ms)}), el("td", {text: sec(a.recu_ms)}),
        el("td", {text: a.type || ""}), el("td", {text: contenu}), el("td", null, [verdict(ep, a, corr, lib)])]));
    });
    t.appendChild(tb);
    return t;
  }

  function listeEnonce(ep, enonce, corr) {
    if (!enonce) return el("p", {"class": "fa-muted", text: "Énoncé non tiré (épreuve pas commencée)."});
    var c = corr || {};
    var lib = libelles(enonce);
    var ol = el("ol");
    function choix(ch) {
      return el("ul", null, (ch || []).map(function (x) {
        var k = c[x.id] || {};
        return el("li", null, [x.texte, k.points !== undefined ? el("span", {"class": "fa-tag", text: k.points + " pt"}) : null,
          ep === "appel" ? el("span", {"class": "fa-muted", text: x.suite ? " → " + x.suite : " → fin de l'appel"}) : null]);
      }));
    }
    if (ep === "radio") {
      (enonce.messages || []).forEach(function (m) {
        ol.appendChild(el("li", null, [m.texte, m.pour_moi ? el("span", {"class": "fa-tag", text: "pour PC Org"}) : null,
          m.priorite ? el("span", {"class": "fa-tag", text: "PRIORITÉ"}) : null]));
      });
    } else if (ep === "appel") {
      (enonce.appels || []).forEach(function (ap) {
        ol.appendChild(el("li", null, [el("b", {text: ap.titre || ap.id}), el("ol", null, (ap.etapes || []).map(function (e) {
          return el("li", null, ["« " + e.appelant + " »", choix(e.choix)]);
        }))]));
      });
    } else if (ep === "logique" || ep === "situations") {
      (enonce.questions || []).forEach(function (q) {
        var att = (c[q.id] || {}).a;
        ol.appendChild(el("li", null, [q.texte, choix(q.choix),
          att ? el("div", {"class": "fa-muted", text: "Attendu : " + (lib[att] || att)}) : null]));
      });
    } else if (ep === "mc") {
      (enonce.fiches || []).forEach(function (f) {
        var ans = (c[f.id] || {}).ans;
        ol.appendChild(el("li", null, [f.texte, ans ? el("div", {"class": "fa-muted",
          text: "Attendu : " + Object.keys(ans).map(function (k) { return k + " " + ans[k]; }).join(", ")}) : null]));
      });
    } else if (ep === "patrouilles") {
      (enonce.INCIDENTS || []).forEach(function (i) {
        ol.appendChild(el("li", null, [i.txt + " (" + i.type + ", " + i.zone + ", à " + i.t + " s, échéance " + i.dl + " s)"]));
      });
    } else if (ep === "acces") {
      ol.appendChild(el("p", {"class": "fa-muted", text: "Heure " + enonce.heure + ", couleurs admises : " + (enonce.couleurs || []).join(", ")}));
      (enonce.badges || []).forEach(function (b) {
        ol.appendChild(el("li", null, [b.col + " " + b.num + " jusqu'à " + b.until,
          el("span", {"class": "fa-tag", text: b.ok ? "à laisser entrer" : "à refuser"})]));
      });
    }
    return ol;
  }

  function blocEpreuve(e, ep, corr) {
    ep = ep || {};
    var lib = libelles(ep.enonce);
    var score = typeof ep.score === "number" ? ep.score + " / 100" : "";
    var duree = ep.debut && ep.fin ? fmtDuree((new Date(ep.fin) - new Date(ep.debut)) / 1000) : "";
    var d = el("details", {"class": "fa-ep"}, [
      el("summary", null, [icone("chevron_right"), NOMS[e], el("span", {"class": "fa-tag", text: ETATS_EP[ep.etat] || ep.etat || ""}),
        (ep.drapeaux || []).length ? el("span", null, ep.drapeaux.map(function (f) { return el("span", {"class": "fa-flag", text: f}); })) : null,
        el("span", {"class": "fa-score", text: score})]),
      el("div", {"class": "fa-ep-b"}, [
        dl([["Début", fmtDate(ep.debut)], ["Fin", fmtDate(ep.fin)], ["Durée réelle", duree]]),
        el("h4", {text: "Énoncé tiré"}), listeEnonce(e, ep.enonce, corr),
        el("h4", {text: "Actions et réponses reçues (" + (ep.actions || []).length + ")"}), tableActions(e, ep.actions, corr, lib),
        ep.detail && Object.keys(ep.detail).length ? el("h4", {text: "Détail du score"}) : null,
        ep.detail && Object.keys(ep.detail).length ? el("pre", {"class": "fa-pre", text: JSON.stringify(ep.detail, null, 2)}) : null
      ])]);
    return d;
  }

  function blocTentative(t, corr, titre) {
    var traces = (t.reprises_trace || []).map(function (r) {
      return fmtDate(r.date) + " après abandon de « " + (NOMS[r.epreuve] || r.epreuve) + " » (IP " + (r.ip || "?") + ")";
    }).join(" ; ");
    var reinit = t.reinitialisation ? fmtDate(t.reinitialisation.date) + " par " + t.reinitialisation.auteur +
      " : " + t.reinitialisation.motif : "";
    var box = el("div", null, [
      el("h3", {text: titre}),
      dl([["Statut", pill(t.statut)], ["Début", fmtDate(t.debut)], ["Fin", fmtDate(t.fin)],
          ["Coefficient", t.coef && t.coef !== 1 ? "x " + fmtNum(t.coef) + " (aménagement)" : "1 (aucun aménagement)"],
          ["Reprises", String(t.reprises || 0) + (traces ? " : " + traces : "")],
          ["Score global", t.global === null || t.global === undefined ? "" : fmtNum(t.global) + " / 100"],
          ["Réinitialisation", reinit]])
    ]);
    EPREUVES.forEach(function (e) { box.appendChild(blocEpreuve(e, (t.epreuves || {})[e], corr[e])); });
    return box;
  }

  function ouvrirDetail(id) {
    ouvert = id;
    corpsTableau();
    var corps = vider($("fa-d-corps"));
    corps.appendChild(el("p", {"class": "fa-muted", text: "Chargement..."}));
    $("fa-detail").hidden = false;
    api("GET", "/tentative/" + encodeURIComponent(id)).then(function (d) {
      if (ouvert !== id) return;
      vider(corps);
      if (!d.ok) { corps.appendChild(el("p", {text: message(d)})); return; }
      var c = d.candidat, t = d.tentative;
      $("fa-d-titre").textContent = (c.prenom || "") + " " + (c.nom || "");
      corps.appendChild(dl([["E-mail", c.email], ["Id Forms", c.forms_id === null ? "" : String(c.forms_id)],
        ["Réponse Forms", fmtDate(c.forms_repondu_le)], ["Fictif", c.fictif ? "oui" : ""]]));

      // Amenagement
      var chk = el("input", {type: "checkbox", id: "fa-d-amen"});
      chk.checked = !!c.amenagement;
      var note = el("div", {"class": "fa-note", text: t
        ? "Tentative déjà commencée : le coefficient a été figé au clic « Commencer » (x " + fmtNum(t.coef) + "). Changer l'aménagement n'a plus d'effet sur elle, seulement sur une nouvelle tentative après réinitialisation."
        : "Le coefficient (réglages) sera figé au clic « Commencer » du candidat ; il allonge tous les délais du test."});
      chk.addEventListener("change", function () {
        chk.disabled = true;
        api("POST", "/candidat/" + encodeURIComponent(id) + "/amenagement", {actif: chk.checked}).then(function (r) {
          chk.disabled = false;
          if (!r.ok) { chk.checked = !chk.checked; toast("error", message(r)); return; }
          toast(r.tentative_commencee ? "warning" : "success", r.tentative_commencee
            ? "Aménagement enregistré, sans effet sur la tentative déjà commencée."
            : "Aménagement " + (r.amenagement ? "activé." : "retiré."));
          chargerCandidats();
        });
      });
      corps.appendChild(el("label", {"class": "fa-switch"}, [chk, "Temps aménagé"]));
      corps.appendChild(note);

      // Reinitialisation
      if (t) {
        corps.appendChild(el("div", {"class": "fa-actions-d"}, [
          el("button", {"class": "fa-btn fa-danger", onclick: function () { reinitialiser(id, c); }},
             [icone("restart_alt"), "Réinitialiser la tentative"])]));
        corps.appendChild(blocTentative(t, d.corrige || {}, "Tentative en cours de validité"));
      } else {
        corps.appendChild(el("p", {"class": "fa-muted", text: "Aucune tentative : le candidat n'a pas commencé."}));
      }
      (d.historique || []).slice().reverse().forEach(function (h, i) {
        var det = el("details", {"class": "fa-ep"}, [el("summary", null, [icone("history"),
          "Tentative réinitialisée " + (i + 1) + " (" + fmtDate(h.debut) + ")"]), el("div", {"class": "fa-ep-b"}, [blocTentative(h, d.corrige || {}, "")])]);
        corps.appendChild(det);
      });
      if ((d.envois || []).length) {
        corps.appendChild(el("h3", {text: "E-mails"}));
        corps.appendChild(el("ul", null, d.envois.map(function (e) {
          return el("li", {text: e.type + " : " + e.statut + " " + fmtDate(e.envoye_le || e.cree_le) + (e.erreur ? " (" + e.erreur + ")" : "")});
        })));
      }
      if ((d.journal || []).length) {
        corps.appendChild(el("h3", {text: "Journal d'administration"}));
        corps.appendChild(el("ul", null, d.journal.map(function (j) {
          var det = j.details || {};
          var txt = j.action === "amenagement" ? "aménagement " + (det.apres ? "activé" : "retiré")
            : j.action === "reinitialisation" ? "réinitialisation : " + det.motif : j.action;
          return el("li", {text: fmtDate(j.t) + " - " + j.auteur + " - " + txt});
        })));
      }
    });
  }

  function fermerDetail() {
    ouvert = null;
    $("fa-detail").hidden = true;
    corpsTableau();
  }

  function reinitialiser(id, c) {
    var ta = el("textarea", {maxlength: "500", placeholder: "Ex. coupure réseau signalée par e-mail le..."});
    var err = el("p", {"class": "fa-ko"});
    var contenu = el("div", null, [
      el("p", {text: "La tentative de " + c.prenom + " " + c.nom + " sera conservée (consultable) et le candidat pourra recommencer de zéro. Action journalisée."}),
      el("label", {"class": "fa-field"}, ["Motif (obligatoire)", ta]), err]);
    (function demander() {
      modale("Réinitialiser la tentative", contenu, "Réinitialiser", "fa-danger").then(function (ok) {
        if (!ok) return;
        if (!ta.value.trim()) { err.textContent = ERREURS.motif_requis; demander(); return; }
        api("POST", "/candidat/" + encodeURIComponent(id) + "/reinitialiser", {motif: ta.value.trim()}).then(function (r) {
          if (!r.ok) { toast("error", message(r)); return; }
          toast("success", "Tentative réinitialisée.");
          chargerCandidats();
          ouvrirDetail(id);
        });
      });
    })();
  }

  // --- Campagne -----------------------------------------------------------------------

  function kpi(v, libelle, mauvais, actif) {
    return el("div", {"class": "fa-kpi" + (mauvais ? " is-bad" : "") + (actif ? " is-on" : "")},
              [el("b", {text: v === null || v === undefined ? "-" : String(v)}), el("span", {text: libelle})]);
  }

  var gabaritCharge = false;
  var gabaritEnregistre = {objet: "", corps: ""};

  function gabaritModifie() {
    var g = gabaritSaisi();
    return g.objet !== gabaritEnregistre.objet || g.corps !== gabaritEnregistre.corps;
  }

  function majBoutonEssai() {
    var modifie = gabaritCharge && gabaritModifie();
    $("fa-essai").disabled = modifie;
    $("fa-essai").title = modifie ? "Enregistrez le gabarit : l'essai envoie le gabarit enregistré, pas le texte modifié." : "";
  }
  function chargerGabarit() {
    if (gabaritCharge) return;
    api("GET", "/reglages").then(function (d) {
      if (!d.ok) return;
      gabaritCharge = true;
      $("fa-g-objet").value = d.gabarit.objet || "";
      $("fa-g-corps").value = d.gabarit.corps || "";
      gabaritEnregistre = {objet: $("fa-g-objet").value, corps: $("fa-g-corps").value};
      majBoutonEssai();
      apercu();
    });
  }

  function gabaritSaisi() { return {objet: $("fa-g-objet").value, corps: $("fa-g-corps").value}; }

  function apercu() {
    api("POST", "/gabarit/apercu", gabaritSaisi()).then(function (d) {
      if (!d.ok) { toast("error", message(d)); return; }
      $("fa-apercu-objet").textContent = "Objet : " + d.objet;
      $("fa-apercu-objet").classList.remove("fa-muted");
      $("fa-apercu").srcdoc = '<!doctype html><meta charset="utf-8"><body style="margin:12px">' + d.html + "</body>";
    });
  }

  function enregistrerGabarit() {
    api("PUT", "/gabarit", gabaritSaisi()).then(function (d) {
      if (!d.ok) { toast("error", message(d)); return; }
      gabaritEnregistre = gabaritSaisi();
      majBoutonEssai();
      toast("success", "Gabarit enregistré.");
      apercu();
    });
  }

  function essai() {
    var a = $("fa-essai-adresse").value.trim();
    if (!a) { toast("warning", ERREURS.adresse_invalide); return; }
    if (gabaritModifie()) { toast("warning", "Enregistrez le gabarit avant l'essai : il envoie le gabarit enregistré."); return; }
    $("fa-essai").disabled = true;
    api("POST", "/campagne/essai", {adresse: a}).then(function (d) {
      majBoutonEssai();
      if (!d.ok) { toast("error", message(d)); return; }
      toast("success", "Essai envoyé à " + a + " (gabarit enregistré).");
    });
  }

  var compte = null;
  function compter() {
    $("fa-lancer").disabled = true;
    compte = null;
    return api("POST", "/campagne/compter", {cible: $("fa-cible").value}).then(function (d) {
      if (!d.ok) { toast("error", message(d)); return; }
      compte = {cible: $("fa-cible").value, n: d.n};
      $("fa-compte-cible").textContent = d.n + " destinataire" + (d.n > 1 ? "s" : "") + " (fictifs exclus).";
      $("fa-lancer").disabled = d.n === 0;
    });
  }

  function lancer() {
    if (!compte || compte.cible !== $("fa-cible").value) { compter(); return; }
    var n = compte.n;
    var libelle = compte.cible === "non_invites" ? "invitation" : "relance";
    var contenu = el("div", null, [
      el("p", null, ["Envoi de la ", el("b", {text: libelle}), " à :"]),
      el("p", {"class": "fa-big", text: n + " candidat" + (n > 1 ? "s" : "")}),
      el("p", {"class": "fa-muted", text: "Un e-mail par destinataire, un toutes les quelques secondes (réglages). Vérifiez le gabarit et l'envoi d'essai avant."})]);
    modale("Confirmer l'envoi", contenu, "Envoyer à " + n + " candidat" + (n > 1 ? "s" : "")).then(function (ok) {
      if (!ok) return;
      api("POST", "/campagne/lancer", {cible: compte.cible, confirmation_n: n}).then(function (d) {
        if (d._status === 409) {
          toast("warning", "Le nombre de destinataires a changé (" + d.n + ") : vérifiez puis relancez.");
          compter(); return;
        }
        if (!d.ok) { toast("error", message(d)); return; }
        toast("success", d.planifies + " e-mail(s) mis en file" + (d.invalides ? ", " + d.invalides + " adresse(s) invalide(s) ignorée(s)" : "") + ".");
        compte = null;
        $("fa-lancer").disabled = true;
        afficherEtat(d.etat);
      });
    });
  }

  function afficherEtat(e) {
    if (!e) return;
    var z = vider($("fa-etat"));
    z.appendChild(kpi(e.en_file, "en file"));
    z.appendChild(kpi(e.envoyes, "envoyés"));
    z.appendChild(kpi(e.echecs, "échecs", e.echecs > 0));
    z.appendChild(kpi(e.actif ? "en marche" : "à l'arrêt", "fil d'envoi", false, e.actif));
    z.appendChild(kpi(e.arretee ? "oui" : "non", "campagne arrêtée", e.arretee));
    $("fa-renvoyer").disabled = !e.echecs;
    $("fa-arreter").disabled = e.arretee && !e.actif;
    $("fa-reprendre").disabled = !e.arretee && (e.actif || !e.en_file);
  }

  function chargerEtat() {
    return api("GET", "/campagne/etat").then(function (d) { if (d.ok) afficherEtat(d.etat); });
  }

  function actionCampagne(chemin, texteOk) {
    return api("POST", "/campagne/" + chemin, {}).then(function (d) {
      if (!d.ok) { toast("error", message(d)); return; }
      toast("success", texteOk(d));
      afficherEtat(d.etat);
    });
  }

  function renvoyer() {
    modale("Renvoyer les échecs", el("p", {text: "Les e-mails en échec repassent en file et partiront au rythme réglé."}), "Renvoyer")
      .then(function (ok) {
        if (ok) actionCampagne("renvoyer-echecs", function (d) { return d.n + " e-mail(s) remis en file."; });
      });
  }

  // --- Resultats -------------------------------------------------------------------------

  var graphiques = [];
  function chargerStats() {
    api("GET", "/stats").then(function (s) {
      if (!s.ok) { toast("error", message(s)); return; }
      var z = vider($("fa-kpis"));
      z.appendChild(kpi(s.inscrits, "inscrits"));
      z.appendChild(kpi(s.invites, "invités (e-mail parti)"));
      z.appendChild(kpi(s.commences, "ont commencé"));
      z.appendChild(kpi(s.en_cours, "en cours"));
      z.appendChild(kpi(s.termines, "terminés"));
      z.appendChild(kpi(s.interrompus, "interrompus"));
      z.appendChild(kpi(s.temps_moyen_s === null ? null : fmtDuree(s.temps_moyen_s), "temps moyen"));
      graphiques.forEach(function (g) { g.destroy(); });
      graphiques = [];
      var zc = vider($("fa-charts"));
      var tranches = [];
      for (var i = 0; i < 10; i++) tranches.push(i * 10 + "-" + (i === 9 ? 100 : i * 10 + 9));
      EPREUVES.forEach(function (e) {
        var canvas = el("canvas", {"aria-label": "Distribution des scores, " + NOMS[e], role: "img"});
        var n = s.distribution[e].reduce(function (a, b) { return a + b; }, 0);
        zc.appendChild(el("div", null, [el("h3", {text: NOMS[e] + " (" + n + " noté" + (n > 1 ? "s" : "") + ")"}),
          el("div", {"class": "fa-chart"}, [canvas])]));
        if (typeof Chart === "undefined") return;
        graphiques.push(new Chart(canvas, {
          type: "bar",
          data: {labels: tranches, datasets: [{label: "Candidats", data: s.distribution[e],
                 backgroundColor: "#2563eb", borderRadius: 3}]},
          options: {responsive: true, maintainAspectRatio: false, animation: false,
                    plugins: {legend: {display: false}},
                    scales: {y: {beginAtZero: true, ticks: {precision: 0}}, x: {grid: {display: false}}}}
        }));
      });
    });
  }

  // --- Reglages ---------------------------------------------------------------------------

  function chargerReglages() {
    api("GET", "/reglages").then(function (d) {
      if (!d.ok) { toast("error", message(d)); return; }
      var r = d.reglages, f = $("fa-reglages");
      f.ouverture.value = r.ouverture_paris || "";
      f.fermeture.value = r.fermeture_paris || "";
      f.coef_amenagement.value = r.coef_amenagement;
      f.debit_envoi_s.value = r.debit_envoi_s;
      f.reply_to.value = r.reply_to || "";
      f.contact_rgpd.value = r.contact_rgpd || "";
      f.expediteur_nom.value = r.expediteur_nom || "";
      var z = vider($("fa-ponds"));
      EPREUVES.forEach(function (e) {
        z.appendChild(el("label", null, [NOMS[e], el("input", {type: "number", min: "0", step: "0.5",
          name: "pond_" + e, value: String((r.ponderations || {})[e] === undefined ? 1 : r.ponderations[e]), required: true})]));
      });
      $("fa-conservation").textContent = "Conservation des données jusqu'au 30/01/2027 : purge manuelle de la campagne après la sélection (rappel affiché à partir du 23/01/2027).";
    });
  }

  function enregistrerReglages(ev) {
    ev.preventDefault();
    var f = ev.target;
    var p = {};
    EPREUVES.forEach(function (e) { p[e] = Number(f["pond_" + e].value); });
    var corps = {ouverture: f.ouverture.value, fermeture: f.fermeture.value,
                 coef_amenagement: Number(f.coef_amenagement.value), debit_envoi_s: Number(f.debit_envoi_s.value),
                 reply_to: f.reply_to.value.trim(), contact_rgpd: f.contact_rgpd.value.trim(),
                 expediteur_nom: f.expediteur_nom.value, ponderations: p};
    api("PUT", "/reglages", corps).then(function (d) {
      if (!d.ok) { toast("error", message(d)); return; }
      toast("success", "Réglages enregistrés.");
      chargerReglages();
    });
  }

  // --- Demarrage ---------------------------------------------------------------------------

  var minuterie = null;
  function sonderEtat() {
    clearInterval(minuterie);
    minuterie = setInterval(function () {
      if (ongletCourant === "campagne" && !document.hidden) chargerEtat();
    }, 3000);
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll(".fa-tab").forEach(function (b) {
      b.addEventListener("click", function () { ouvrirOnglet(b.getAttribute("data-tab")); });
    });
    $("fa-import").addEventListener("submit", importer);
    $("fa-f-statut").addEventListener("change", chargerCandidats);
    $("fa-f-fictifs").addEventListener("change", chargerCandidats);
    var att = null;
    $("fa-f-q").addEventListener("input", function () { clearTimeout(att); att = setTimeout(chargerCandidats, 250); });
    $("fa-d-fermer").addEventListener("click", fermerDetail);
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && $("fa-modal").hidden && !$("fa-detail").hidden) fermerDetail();
    });
    $("fa-g-apercu").addEventListener("click", apercu);
    $("fa-g-enregistrer").addEventListener("click", enregistrerGabarit);
    $("fa-essai").addEventListener("click", essai);
    $("fa-g-objet").addEventListener("input", majBoutonEssai);
    $("fa-g-corps").addEventListener("input", majBoutonEssai);
    $("fa-cible").addEventListener("change", function () { compte = null; $("fa-lancer").disabled = true;
      $("fa-compte-cible").textContent = "Comptez d'abord les destinataires."; });
    $("fa-compter").addEventListener("click", compter);
    $("fa-lancer").addEventListener("click", lancer);
    $("fa-arreter").addEventListener("click", function () {
      actionCampagne("arreter", function () { return "Campagne arrêtée : les e-mails en file restent en file."; }); });
    $("fa-reprendre").addEventListener("click", function () {
      actionCampagne("reprendre", function () { return "Campagne reprise."; }); });
    $("fa-renvoyer").addEventListener("click", renvoyer);
    $("fa-reglages").addEventListener("submit", enregistrerReglages);
    chargerBandeau();
    sonderEtat();
    var h = (location.hash || "").slice(1);
    ouvrirOnglet(["candidats", "campagne", "resultats", "reglages"].indexOf(h) >= 0 ? h : "candidats");
  });
})();
