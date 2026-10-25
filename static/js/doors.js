/* Page Portes (poste operateur) -- /doors?event=&year=
 *
 * Trois vues sur la meme API (portes_etat.py) :
 *   Direct : etat courant, rafraichi chaque minute ;
 *   Rejeu  : n'importe quel jour / quart d'heure de l'edition (meme calcul,
 *            arrete a l'instant choisi : ce qu'on aurait vu a ce moment-la) ;
 *   Bilan  : RETEX par jour et par porte (/api/portes/bilan).
 *
 * Une porte n'a pas de mode fixe : elle change de colonne quand ses
 * appareils basculent (entree le matin, sortie a la fin de la course). La
 * charge d'ENTREE se lit face a la capacite de SECURITE (agents prevus x
 * 350/h) quand la porte en a ; la sortie face a ses appareils en sortie.
 *
 * Aucune donnee ne passe par innerHTML : noms de portes et textes de fiches
 * viennent de sources externes (HSH, main courante).
 */
(function () {
  "use strict";

  var PERIODE_MS = 60000;
  // Edition : ?event=&year= de l'URL, poses par le gabarit (meme contrat que
  // /terrains et /general_stat).
  var EVENT = window.EVENT;
  var YEAR = window.YEAR;

  // Memes teintes que doors.css (theme clair Cockpit)
  var COUL = { entree: "#2563eb", sortie: "#ea580c", mixte: "#7c3aed", ferme: "#94a3b8" };
  var NIVEAU_COUL = { saturation: "#dc2626", saturation_prevue: "#ea580c", vigilance: "#d97706", normal: "#16a34a" };
  var NIVEAU_MOT = { saturation: "Saturation", saturation_prevue: "Saturation prévue", vigilance: "Vigilance" };
  var MODE_MOT = { entree: "Entrée", sortie: "Sortie", mixte: "Mixte" };

  var vue = "direct";
  var dernier = null;       // derniere reponse /api/portes/etat
  var choisie = null;       // nom de la porte analysee
  var graphe = null;
  var minuteur = null;
  var bilanData = null, bilanJour = null;
  var editions = null;      // [{event, year}] suivies par le controle d'acces live
  var focus = null;         // analyse complete : {porte, jour (null = edition), a, origine}
  var graphesFocus = [];

  var $ = function (id) { return document.getElementById(id); };

  function el(tag, cls, texte) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (texte !== undefined && texte !== null) e.textContent = texte;
    return e;
  }
  function icone(nom) { return el("span", "material-symbols-outlined", nom); }
  function vider(e) { while (e.firstChild) e.removeChild(e.firstChild); }
  function fmt(n) {
    if (n === null || n === undefined) return "--";
    return Math.round(n).toLocaleString("fr-FR");
  }
  function pct(c) { return c === null || c === undefined ? "--" : Math.round(c * 100) + " %"; }
  function couleurCharge(c) {
    if (c === null || c === undefined) return "#94a3b8";
    if (c >= 1) return NIVEAU_COUL.saturation;
    if (c >= 0.8) return NIVEAU_COUL.vigilance;
    return NIVEAU_COUL.normal;
  }
  function qs(params) {
    var p = new URLSearchParams();
    Object.keys(params).forEach(function (k) { if (params[k] !== null && params[k] !== undefined && params[k] !== "") p.set(k, params[k]); });
    return p.toString();
  }
  function heureDe(i) { return String(Math.floor(i / 4)).padStart(2, "0") + ":" + String((i % 4) * 15).padStart(2, "0"); }

  // ------------------------------------------------------------------------
  // Chargement

  function charger() {
    var params = { event: EVENT, year: YEAR };
    if (vue === "rejeu") {
      params.jour = $("rejeu-jour").value;
      params.a = heureDe(+$("rejeu-heure").value);
    }
    fetch("/api/portes/etat?" + qs(params), { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        dernier = d;
        rendreEtat(d);
        if (focus && focus.origine !== "bilan") rendreFocus();
        else if (focusInitial && d.disponible) { focusInitial = false; ouvrirFocus(choisie, d.jour, vue === "rejeu" ? d.a : null, vue); }
      })
      .catch(function (e) {
        console.error("[Portes]", e);
        fraicheur(false, "liaison perdue");
      });
  }

  function fraicheur(ok, texte) {
    var puce = document.querySelector("#fraicheur .puce");
    puce.className = "puce " + (ok ? "ok" : "ko");
    $("fraicheur-txt").textContent = texte;
  }

  // ------------------------------------------------------------------------
  // Etat (Direct / Rejeu)

  function rendreEtat(d) {
    var vide = !d.disponible;
    $("vide-edition").hidden = !vide;
    document.querySelector(".etat-grille").hidden = vide;
    $("kpis").hidden = vide;
    if (vide) {
      rendreVide();
      fraicheur(false, "aucune donnée");
      return;
    }
    if (vue === "direct") {
      fraicheur(d.direct, d.direct ? ("direct · mesure jusqu'à " + d.a)
        : ("édition hors direct · " + d.jour + " " + d.a));
    } else {
      fraicheur(true, "rejeu · " + d.jour + " " + d.a);
    }
    remplirJours(d);
    rendreKpis(d);
    rendreListe(d);
    var p = choisie && d.portes.filter(function (x) { return x.porte === choisie; })[0];
    if (p) rendreDetail(p, d); else viderDetail();
  }

  function kpi(lib, val, unite, sous, cls) {
    var k = el("div", "kpi" + (cls ? " " + cls : ""));
    k.appendChild(el("div", "lib", lib));
    var v = el("div", "val");
    if (val instanceof Node) v.appendChild(val); else v.textContent = val;
    if (unite) v.appendChild(el("small", null, unite));
    k.appendChild(v);
    if (sous) k.appendChild(el("div", "sous", sous));
    return k;
  }

  function rendreKpis(d) {
    var h = $("kpis"), t = d.totaux;
    vider(h);
    h.appendChild(kpi("Entrant", fmt(t.debit_entree), "/h", "15 dernières minutes"));
    h.appendChild(kpi("Sortant", fmt(t.debit_sortie), "/h", "borne basse à l'exode"));
    var modes = el("span");
    [["entree", "entrée"], ["mixte", "mixte"], ["sortie", "sortie"]].forEach(function (m) {
      var s = el("span");
      var n = el("b", null, String(t.par_mode[m[0]] || 0));
      n.style.color = COUL[m[0]];
      s.appendChild(n); s.appendChild(el("i", null, m[1]));
      modes.appendChild(s);
    });
    h.appendChild(kpi("Portes actives", modes, null, t.portes_fermees + " fermée(s) · " + t.appareils + " appareils", "modes"));
    if (d.presents !== null && d.presents !== undefined) h.appendChild(kpi("Présents", fmt(d.presents), null, "enceinte générale"));
    var nbAl = d.alertes.filter(function (a) { return a.niveau === "critique" || a.niveau === "danger"; }).length;
    var al = kpi("Alertes", String(nbAl), null, nbAl ? d.alertes[0].porte : "aucune saturation");
    if (nbAl) al.querySelector(".val").style.color = NIVEAU_COUL.saturation;
    h.appendChild(al);
    var n1 = d.n1 || {};
    h.appendChild(kpi("Référence N-1", n1.annee ? String(n1.annee) : "aucune", null,
      n1.annee ? ({ archive_live: "archive live 5 min", import_15min: "import 15 min, par sens",
                    portes_total: "import horaire, sens confondus" }[n1.source] || n1.source)
               + (n1.decalage_jours ? " · décalage " + n1.decalage_jours + " j" : "")
               : "pas de prévision"));
    if (d.jour_course !== null && d.jour_course !== undefined) {
      var jc = d.jour_course === 0 ? "Jour de course" : ("J" + (d.jour_course > 0 ? "+" : "") + d.jour_course);
      h.appendChild(kpi("Jour", jc, null, new Date(d.jour + "T12:00").toLocaleDateString("fr-FR", { weekday: "long", day: "numeric", month: "long" })));
    }
    $("leg-note").textContent = d.securite ? "charge d'entrée = capacité sécurité (agents × " + d.seuils.agent_h + "/h)"
      : "pas de planning sécurité : charge sur la capacité des appareils";
  }

  function rendreListe(d) {
    var h = $("groupes");
    vider(h);
    var groupes = [
      { cle: "entree", titre: "En entrée" }, { cle: "mixte", titre: "Mixtes" },
      { cle: "sortie", titre: "En sortie" }, { cle: "ferme", titre: "Fermées / sans passage" }
    ];
    groupes.forEach(function (g) {
      var portes = d.portes.filter(function (p) {
        return g.cle === "ferme" ? (p.fermee || !p.mode) : (!p.fermee && p.mode === g.cle);
      });
      if (!portes.length) return;
      var t = el("div", "groupe-titre");
      var past = el("span", "pastille"); past.style.background = COUL[g.cle];
      t.appendChild(past); t.appendChild(el("span", null, g.titre)); t.appendChild(el("span", "nb", "(" + portes.length + ")"));
      h.appendChild(t);
      portes.forEach(function (p) { h.appendChild(ligne(p)); });
    });
  }

  function ligne(p) {
    var r = el("div", "porte" + (p.porte === choisie ? " choisie" : ""));
    var lis = el("div", "lisere");
    lis.style.background = p.fermee ? COUL.ferme : (NIVEAU_COUL[p.niveau] || "#22c55e");
    r.appendChild(lis);

    var g = el("div");
    var nom = el("div", "nom", p.porte);
    if (NIVEAU_MOT[p.niveau]) nom.appendChild(el("span", "badge " + p.niveau, NIVEAU_MOT[p.niveau]));
    if (p.pda_renfort) nom.appendChild(el("span", "badge info", "PDA renfort"));
    if (p.uam && p.uam.length) nom.appendChild(el("span", "badge info", "UAM"));
    if (p.calme && !p.fermee) nom.appendChild(el("span", "badge info", "calme"));
    g.appendChild(nom);
    var inf = el("div", "infos");
    var a = p.appareils;
    inf.appendChild(txtB("app. ", (a.entree + a.deux_sens) + "E / " + (a.sortie + a.deux_sens) + "S"));
    if (p.capacite.agents) inf.appendChild(txtB("sécu ", p.capacite.agents + " ag."));
    if (p.refus_pct !== null && p.refus_pct >= 5) inf.appendChild(txtB("refus ", p.refus_pct + " %"));
    if (p.fiches && p.fiches.length) inf.appendChild(txtB("fiches ", String(p.fiches.length)));
    if (p.fermee && p.derniere_activite) inf.appendChild(el("span", null, "dernier passage " + p.derniere_activite));
    g.appendChild(inf);
    r.appendChild(g);

    var dr = el("div", "droite");
    var deb = el("div", "debits");
    deb.appendChild(el("span", "e", "↓ " + fmt(p.debit.entree)));
    deb.appendChild(el("span", "s", "↑ " + fmt(p.debit.sortie)));
    dr.appendChild(deb);
    // Jauge du sens dominant : entree (securite ou appareils) ou sortie
    var sensJ = p.mode === "sortie" ? "sortie" : "entree";
    var c = p.charge[sensJ];
    var j = el("div", "jauge"), b = el("span");
    b.style.width = Math.min(100, Math.round((c || 0) * 100)) + "%";
    b.style.background = couleurCharge(c);
    j.appendChild(b); dr.appendChild(j);
    dr.appendChild(el("div", "jauge-lib", sensJ + " " + pct(c) +
      (sensJ === "entree" && p.charge.source_entree === "securite" ? " sécu" : "")));
    r.appendChild(dr);

    r.addEventListener("click", function () {
      choisie = p.porte;
      document.querySelectorAll(".porte.choisie").forEach(function (x) { x.classList.remove("choisie"); });
      r.classList.add("choisie");
      rendreDetail(p, dernier);
    });
    return r;
  }
  function txtB(lib, val) {
    var s = el("span", null, lib);
    s.appendChild(el("b", null, val));
    return s;
  }

  // ------------------------------------------------------------------------
  // Detail d'une porte

  function viderDetail() {
    $("detail-vide").hidden = false;
    $("detail-contenu").hidden = true;
    if (graphe) { graphe.destroy(); graphe = null; }
  }

  function dkpi(lib, val, unite, note, couleur) {
    var k = el("div", "d-kpi");
    k.appendChild(el("div", "lib", lib));
    var v = el("div", "val", val);
    if (couleur) v.style.color = couleur;
    if (unite) v.appendChild(el("small", null, " " + unite));
    k.appendChild(v);
    if (note) k.appendChild(el("div", "note", note));
    return k;
  }

  function section(titre, aide) {
    var s = el("div", "d-section");
    var h = el("h3", null, titre);
    if (aide) h.appendChild(el("span", "aide", aide));
    s.appendChild(h);
    return s;
  }

  function rendreDetail(p, d, cible) {
    var h = cible || $("detail-contenu");
    if (!cible) { $("detail-vide").hidden = true; h.hidden = false; }
    vider(h);

    var tete = el("div", "d-tete");
    tete.appendChild(el("h2", null, p.porte));
    var m = p.fermee ? "ferme" : (p.mode || "ferme");
    tete.appendChild(el("span", "badge mode " + m, p.fermee ? "Fermée" : (MODE_MOT[p.mode] || "--")));
    if (NIVEAU_MOT[p.niveau]) tete.appendChild(el("span", "badge " + p.niveau, NIVEAU_MOT[p.niveau]));
    if (p.pda_renfort) tete.appendChild(el("span", "badge info", "PDA en renfort des tripodes"));
    if (p.uam && p.uam.length) tete.appendChild(el("span", "badge info", "UAM : " + p.uam.join(", ")));
    if (!cible) {
      var ouv = el("button", "d-ouvrir");
      ouv.type = "button";
      ouv.appendChild(icone("open_in_full"));
      ouv.appendChild(document.createTextNode("Analyse complète"));
      ouv.title = "Toutes les données de la porte : refus, doubles scans, entrées sans sortie, appareils, titres...";
      ouv.addEventListener("click", function () { ouvrirFocus(p.porte, d.jour, vue === "rejeu" ? d.a : null, vue); });
      tete.appendChild(ouv);
    }
    tete.appendChild(el("div", "sous", "État à " + d.a + " le " + d.jour + " · fenêtre de " + d.seuils.fenetre_min +
      " min · " + (p.debit.part_sorties !== null ? p.debit.part_sorties + " % de sorties" : "aucun passage") +
      (p.debit.part_vehicules ? " · " + p.debit.part_vehicules + " % de véhicules" : "")));
    h.appendChild(tete);

    var k = el("div", "d-kpis");
    k.appendChild(dkpi("Entrée", fmt(p.debit.entree), "/h", fmt(p.debit.entree_personnes) + " personnes/h"));
    k.appendChild(dkpi("Sortie", fmt(p.debit.sortie), "/h", "borne basse à l'exode"));
    if (p.capacite.securite) {
      k.appendChild(dkpi("Charge sécurité", pct(p.charge.securite), null,
        p.capacite.agents + " agents prévus = " + fmt(p.capacite.securite) + "/h", couleurCharge(p.charge.securite)));
    }
    k.appendChild(dkpi("Appareils entrée", pct(p.charge.scan_entree), null,
      "capacité " + fmt(p.capacite.scan_entree) + "/h", couleurCharge(p.charge.scan_entree)));
    k.appendChild(dkpi("Appareils sortie", pct(p.charge.sortie), null,
      "capacité " + fmt(p.capacite.scan_sortie) + "/h", couleurCharge(p.charge.sortie)));
    k.appendChild(dkpi("Refus", p.refus_pct === null ? "--" : p.refus_pct + " %", null, "scans refusés, 15 min",
      p.refus_pct >= 15 ? NIVEAU_COUL.vigilance : null));
    h.appendChild(k);

    // Graphe : entrees vers le haut, sorties vers le bas (le mode se lit a
    // la forme), N-1 en pointilles, capacite de securite en marches.
    var sg = section("Débits par quart d'heure", "entrées vers le haut · sorties vers le bas · N-1 aligné sur la course en pointillés · capacité sécurité en marches");
    var box = el("div", "graphe");
    var cv = el("canvas");
    box.appendChild(cv); sg.appendChild(box);
    h.appendChild(sg);

    var sr = section("Mode au fil de la journée", "un quart d'heure par case");
    sr.appendChild(ruban(p.series || [], d.a));
    var axe = el("div", "ruban-axe");
    ["00h", "04h", "08h", "12h", "16h", "20h", "24h"].forEach(function (x) { axe.appendChild(el("span", null, x)); });
    sr.appendChild(axe);
    h.appendChild(sr);

    var deux = el("div", "d-deux");
    deux.appendChild(blocPrevision(p, d));
    deux.appendChild(blocAppareils(p));
    h.appendChild(deux);

    var deux2 = el("div", "d-deux");
    deux2.appendChild(blocFiches(p));
    deux2.appendChild(blocProgramme(p, d));
    h.appendChild(deux2);

    dessinerGraphe(cv, p, d);
  }

  function ruban(series, aHeure) {
    var r = el("div", "ruban");
    var idxA = Math.floor(((+aHeure.slice(0, 2)) * 60 + (+aHeure.slice(3, 5))) / 15) - 1;
    for (var i = 0; i < 96; i++) {
      var s = series[i] || {};
      var c = el("i", s.e === undefined ? "futur" : (s.mode || ""));
      c.title = heureDe(i) + (s.mode ? " · " + MODE_MOT[s.mode] + " · ↓" + s.e + "/h ↑" + s.s + "/h" : "");
      if (i === idxA) c.classList.add("curseur");
      r.appendChild(c);
    }
    return r;
  }

  function blocPrevision(p, d) {
    var s = section("Prévision", "débit courant × profil N-1 de la porte");
    var pv = p.prevision;
    if (!pv.entree.length && !pv.sortie.length) {
      s.appendChild(el("p", "note", d.n1 && d.n1.annee
        ? (d.n1.source === "portes_total"
           ? "Pas de prévision : le N-1 disponible (import horaire) mélange entrées et sorties. La prévision par sens viendra avec les archives live 2026 comme référence."
           : "Pas de prévision : flux trop faible ou profil N-1 insuffisant à cette heure.")
        : "Pas de prévision : aucune édition N-1 exploitable."));
      return s;
    }
    var w = el("div", "prev");
    [["entree", "↓"], ["sortie", "↑"]].forEach(function (x) {
      pv[x[0]].forEach(function (pt) {
        var b = el("div", "p");
        b.appendChild(el("span", null, x[1] + " +" + pt[0] + " min "));
        b.appendChild(el("b", null, fmt(pt[1])));
        b.appendChild(el("span", null, "/h"));
        b.style.borderLeft = "3px solid " + COUL[x[0]];
        w.appendChild(b);
      });
    });
    s.appendChild(w);
    if (pv.pire) {
      var txt = "Pire charge attendue : " + pct(pv.pire.charge) + " en " + pv.pire.sens + " dans " + pv.pire.dans_min + " min" +
        (pv.pire.agents ? " (" + pv.pire.agents + " agents prévus à ce moment-là)" : "");
      var n = el("p", "note", txt);
      if (pv.pire.charge >= 1) n.style.color = NIVEAU_COUL.saturation;
      s.appendChild(n);
    }
    return s;
  }

  function blocAppareils(p) {
    var s = section("Appareils actifs", p.appareils.tripodes + " tripode(s) · " + p.appareils.pda + " PDA");
    if (!p.appareils.liste.length) { s.appendChild(el("p", "note", "Aucun appareil actif sur la fenêtre.")); return s; }
    var t = el("table", "mini");
    var th = el("tr");
    ["Appareil", "Type", "Sens", "Débit"].forEach(function (x) { th.appendChild(el("th", null, x)); });
    t.appendChild(th);
    p.appareils.liste.forEach(function (a) {
      var tr = el("tr");
      tr.appendChild(el("td", null, a.nom));
      tr.appendChild(el("td", null, a.type));
      var td = el("td"); td.appendChild(el("span", "sens " + a.sens, a.sens)); tr.appendChild(td);
      tr.appendChild(el("td", "num", fmt(a.debit) + "/h"));
      t.appendChild(tr);
    });
    s.appendChild(t);
    return s;
  }

  function blocFiches(p) {
    var s = section("Main courante", "fiches flux / sécurité du jour citant la porte");
    var f = p.fiches || [];
    if (!f.length) { s.appendChild(el("p", "note", "Aucune fiche citant cette porte.")); return s; }
    var ul = el("ul", "liste-simple");
    f.forEach(function (x) {
      var li = el("li", x.ouverte ? "ouverte" : null);
      li.appendChild(el("span", "h", x.heure));
      li.appendChild(el("span", null, x.texte + (x.categorie ? " (" + x.categorie.replace("PCO.", "") + ")" : "")));
      ul.appendChild(li);
    });
    s.appendChild(ul);
    return s;
  }

  function blocProgramme(p, d) {
    var s = section("Programme", "vignettes de la timeline : la porte et le général");
    var v = (d.vignettes || []).filter(function (x) { return !x.porte || x.porte === p.porte; });
    if (!v.length) { s.appendChild(el("p", "note", "Aucune vignette ce jour.")); return s; }
    var ul = el("ul", "liste-simple");
    v.forEach(function (x) {
      var li = el("li");
      li.appendChild(el("span", "h", x.debut || "--"));
      var t = el("span", null, x.libelle + (x.fin ? " (jusqu'à " + x.fin + ")" : ""));
      if (x.porte) t.style.fontWeight = "700";
      li.appendChild(t);
      ul.appendChild(li);
    });
    s.appendChild(ul);
    return s;
  }

  function dessinerGraphe(cv, p, d) {
    if (graphe) { graphe.destroy(); graphe = null; }
    if (typeof Chart === "undefined") return;
    var s = p.series || [];
    var lab = s.map(function (x) { return x.t; });
    var e = s.map(function (x) { return x.e === undefined ? null : x.e; });
    var so = s.map(function (x) { return x.s === undefined ? null : -x.s; });
    var n1e = s.map(function (x) { return x.n1_e; });
    var n1s = s.map(function (x) { return x.n1_s === null || x.n1_s === undefined ? null : -x.n1_s; });
    var n1t = s.map(function (x) { return x.n1_total; });
    var cap = s.map(function (x) { return x.cap_secu; });
    var capE = s.map(function (x) { return x.cap_e === undefined ? null : x.cap_e; });
    var idxA = lab.indexOf(d.a.slice(0, 3) + String(Math.floor((+d.a.slice(3, 5)) / 15) * 15).padStart(2, "0"));
    var ds = [
      { type: "bar", label: "Entrées /h", data: e, backgroundColor: "rgba(37,99,235,0.70)", stack: "m", barPercentage: 1, categoryPercentage: 0.95 },
      { type: "bar", label: "Sorties /h", data: so, backgroundColor: "rgba(234,88,12,0.70)", stack: "m", barPercentage: 1, categoryPercentage: 0.95 },
      { type: "line", label: "N-1 entrées", data: n1e, borderColor: "#1e3a8a", borderDash: [5, 4], borderWidth: 1.5, pointRadius: 0, spanGaps: false },
      { type: "line", label: "N-1 sorties", data: n1s, borderColor: "#9a3412", borderDash: [5, 4], borderWidth: 1.5, pointRadius: 0, spanGaps: false },
      { type: "line", label: "Capacité appareils entrée", data: capE, borderColor: "#94a3b8", borderWidth: 1, pointRadius: 0, stepped: true }
    ];
    if (n1t.some(function (x) { return x; })) {
      // Import horaire N-1 : passages tous sens confondus, impossible a
      // ranger en entrees ou en sorties.
      // Masquee par defaut : sa vague de sortie (exode) ecrase l'echelle ;
      // un clic sur la legende l'affiche
      ds.push({ type: "line", label: "N-1 tous sens", data: n1t, borderColor: "#475569", borderDash: [2, 3], borderWidth: 1.5, pointRadius: 0, hidden: true });
    }
    if (cap.some(function (x) { return x; })) {
      ds.push({ type: "line", label: "Capacité sécurité", data: cap, borderColor: "#dc2626", borderWidth: 2, pointRadius: 0, stepped: true });
    }
    // Une serie vide (pas de N-1 par sens, pas de planning) encombrerait la legende.
    ds = ds.filter(function (x) { return x.data.some(function (v) { return v !== null && v !== undefined; }); });
    graphe = new Chart(cv.getContext("2d"), {
      data: { labels: lab, datasets: ds },
      options: {
        responsive: true, maintainAspectRatio: false, animation: false,
        interaction: { mode: "index", intersect: false },
        scales: {
          x: { stacked: true, ticks: { color: "#607286", maxTicksLimit: 13 }, grid: { color: "rgba(96,114,134,0.10)" } },
          y: { ticks: { color: "#607286", callback: function (v) { return Math.abs(v).toLocaleString("fr-FR"); } },
               grid: { color: function (c) { return c.tick.value === 0 ? "rgba(31,45,61,0.45)" : "rgba(96,114,134,0.10)"; } } }
        },
        plugins: {
          legend: { labels: { color: "#1f2d3d", boxWidth: 12, font: { size: 11, family: "Outfit" } } },
          tooltip: { callbacks: { label: function (c) {
            return c.dataset.label + " : " + (c.raw === null ? "--" : Math.abs(c.raw).toLocaleString("fr-FR"));
          } } }
        }
      },
      plugins: [{
        id: "curseur",
        afterDatasetsDraw: function (ch) {
          if (idxA < 0) return;
          // Bord gauche du quart d'heure qui commence a l'instant affiche :
          // tout ce qui est a gauche est mesure, a droite seul le N-1 existe.
          var x = ch.scales.x.getPixelForValue(idxA) - (ch.scales.x.getPixelForValue(1) - ch.scales.x.getPixelForValue(0)) / 2;
          var ctx = ch.ctx;
          ctx.save();
          ctx.strokeStyle = "rgba(31,45,61,0.55)"; ctx.setLineDash([3, 3]);
          ctx.beginPath(); ctx.moveTo(x, ch.chartArea.top); ctx.lineTo(x, ch.chartArea.bottom); ctx.stroke();
          ctx.restore();
        }
      }]
    });
  }

  // ------------------------------------------------------------------------
  // Rejeu

  function remplirJours(d) {
    var sel = $("rejeu-jour");
    if (sel.options.length > 1 || !d.fenetre) return;
    vider(sel);
    var a = new Date(d.fenetre[0] + "T12:00"), b = new Date(d.fenetre[1] + "T12:00");
    for (var t = a; t <= b; t = new Date(t.getTime() + 86400000)) {
      var iso = t.getFullYear() + "-" + String(t.getMonth() + 1).padStart(2, "0") + "-" + String(t.getDate()).padStart(2, "0");
      var o = el("option", null, t.toLocaleDateString("fr-FR", { weekday: "short", day: "numeric", month: "short" }));
      o.value = iso;
      sel.appendChild(o);
    }
    sel.value = d.jour;
    var hm = d.a.split(":");
    $("rejeu-heure").value = Math.min(95, Math.floor((+hm[0] * 60 + +hm[1]) / 15));
    $("rejeu-h").textContent = heureDe(+$("rejeu-heure").value);
  }

  var attenteRejeu = null;
  $("rejeu-heure").addEventListener("input", function () {
    $("rejeu-h").textContent = heureDe(+this.value);
    clearTimeout(attenteRejeu);
    attenteRejeu = setTimeout(charger, 250);
  });
  $("rejeu-jour").addEventListener("change", charger);

  // ------------------------------------------------------------------------
  // Bilan RETEX

  function chargerBilan() {
    var ev = EVENT, yr = YEAR;
    fraicheur(true, "bilan · chargement");
    fetch("/api/portes/bilan?" + qs({ event: ev, year: yr }), { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (b) {
        bilanData = b;
        bilanJour = null;
        if (b.disponible && b.jours.length) {
          var course = b.jours.filter(function (j) { return j.jour_course === 0; })[0];
          bilanJour = (course || b.jours[b.jours.length - 1]).jour;
        }
        rendreBilan();
        fraicheur(true, "bilan · " + ev + " " + yr);
        if (focusInitial && b.disponible) { focusInitial = false; ouvrirFocus(choisie, bilanJour, null, "bilan"); }
      })
      .catch(function (e) { console.error("[Portes bilan]", e); fraicheur(false, "bilan indisponible"); });
  }

  // Selecteur d'edition de l'en-tete : editions suivies par le controle
  // d'acces live, plus celle de l'URL. Changer d'edition recharge la page
  // dans le meme onglet (meme contrat ?event=&year= que le menu).
  function remplirEditions() {
    fetch("/api/portes/editions", { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (rows) {
        editions = rows || [];
        var sel = $("edition-select");
        vider(sel);
        var courant = EVENT + "|" + YEAR, vu = false;
        editions.forEach(function (r) {
          var o = el("option", null, r.event + " " + r.year);
          o.value = r.event + "|" + r.year;
          if (o.value === courant) vu = true;
          sel.appendChild(o);
        });
        if (!vu && EVENT) {
          var o = el("option", null, EVENT + " " + YEAR + " (sans contrôle live)");
          o.value = courant;
          sel.insertBefore(o, sel.firstChild);
        }
        sel.value = courant;
        if (!$("vide-edition").hidden) rendreVide();
      })
      .catch(function (e) { console.error("[Portes editions]", e); });
  }
  function urlEdition(ev, yr) {
    var q = { event: ev, year: yr };
    if (vue !== "direct") q.vue = vue;
    return "/doors?" + qs(q);
  }
  $("edition-select").addEventListener("change", function () {
    var p = this.value.split("|");
    window.location.href = urlEdition(p[0], p[1]);
  });

  function rendreVide() {
    $("vide-titre").textContent = "Aucune donnée de contrôle d'accès live pour " + EVENT + " " + YEAR;
    $("vide-texte").textContent = "Le direct, le rejeu et le bilan s'appuient sur les passages enregistrés par le collecteur du contrôle d'accès (HSH). " +
      "Cette édition n'en a pas : elle n'a pas encore eu lieu, ou elle date d'avant le collecteur. " +
      (editions && editions.length ? "Éditions disponibles :" : "");
    var box = $("vide-editions");
    vider(box);
    (editions || []).forEach(function (r) {
      var a = el("a", null, r.event + " " + r.year);
      a.href = urlEdition(r.event, r.year);
      box.appendChild(a);
    });
  }

  function rendreBilan() {
    var hj = $("bilan-jours"), t = $("bilan-table");
    vider(hj); vider(t);
    var b = bilanData;
    if (!b || !b.disponible) {
      $("bilan-note").textContent = "";
      $("vue-bilan").hidden = true;
      $("vue-etat").hidden = false;
      $("vide-edition").hidden = false;
      document.querySelector(".etat-grille").hidden = true;
      $("kpis").hidden = true;
      rendreVide();
      return;
    }
    b.jours.forEach(function (j) {
      var bt = el("button", "bilan-jour" + (j.jour === bilanJour ? " actif" : ""));
      bt.type = "button";
      var jc = j.jour_course === null ? "" : (j.jour_course === 0 ? " · course" : " · J" + (j.jour_course > 0 ? "+" : "") + j.jour_course);
      bt.appendChild(el("div", "j", new Date(j.jour + "T12:00").toLocaleDateString("fr-FR", { weekday: "long", day: "numeric" }) + jc));
      bt.appendChild(el("div", "n", fmt(j.entrees) + " entrées · " + fmt(j.sorties) + " sorties"));
      bt.addEventListener("click", function () { bilanJour = j.jour; rendreBilan(); });
      hj.appendChild(bt);
    });
    var jour = b.jours.filter(function (j) { return j.jour === bilanJour; })[0];
    if (!jour) return;
    var cols = ["Porte", "Entrées", "Sorties", "Véhicules", "Pic entrée", "Pic sortie", "Modes", "Charge sécu max",
                "Saturation", "Agents max", "PDA renfort", "Refus"];
    var tr = el("tr");
    cols.forEach(function (c) { tr.appendChild(el("th", null, c)); });
    t.appendChild(tr);
    jour.portes.forEach(function (r) {
      var l = el("tr");
      var nomC = el("td", "porte-nom");
      var lien = el("span", "porte-lien", r.porte);
      lien.title = "Analyse complète de la porte ce jour-là";
      lien.addEventListener("click", function () { ouvrirFocus(r.porte, bilanJour, null, "bilan"); });
      nomC.appendChild(lien);
      l.appendChild(nomC);
      l.appendChild(el("td", null, fmt(r.entrees)));
      l.appendChild(el("td", null, fmt(r.sorties)));
      l.appendChild(el("td", null, r.vehicules ? fmt(r.vehicules) : "--"));
      l.appendChild(cellPic(r.pic_entree, r.pic_entree_a));
      l.appendChild(cellPic(r.pic_sortie, r.pic_sortie_a));
      l.appendChild(cellModes(r.minutes_mode));
      var c = el("td");
      if (r.charge_secu_max !== null) {
        var v = el("b", null, pct(r.charge_secu_max));
        v.style.color = couleurCharge(r.charge_secu_max);
        c.appendChild(v); c.appendChild(el("small", null, " " + r.charge_secu_max_a));
      } else c.textContent = "--";
      l.appendChild(c);
      var sat = el("td", null, r.minutes_saturation ? dureeTxt(r.minutes_saturation) : "--");
      if (r.minutes_saturation) sat.style.color = NIVEAU_COUL.saturation;
      l.appendChild(sat);
      l.appendChild(el("td", null, r.agents_max ? String(r.agents_max) : "--"));
      l.appendChild(el("td", null, r.pda_renfort_min ? dureeTxt(r.pda_renfort_min) : "--"));
      l.appendChild(el("td", null, r.refus_pct === null ? "--" : r.refus_pct + " %"));
      t.appendChild(l);
    });
    $("bilan-note").textContent = "Débits de pic en passages/h sur un quart d'heure. Charge sécurité = entrées de personnes face aux agents prévus au planning × " +
      b.seuils.agent_h + "/h ; « saturation » = temps passé au-dessus de 100 %. Les sorties sont une borne basse à l'exode (contrôle souvent levé)." +
      (b.securite ? "" : " Aucun planning sécurité pour cette édition.");
  }
  function cellPic(v, a) {
    var c = el("td");
    if (!v) { c.textContent = "--"; return c; }
    c.appendChild(el("b", null, fmt(v)));
    c.appendChild(el("small", null, " " + a));
    return c;
  }
  function cellModes(m) {
    var c = el("td");
    var tot = (m.entree || 0) + (m.mixte || 0) + (m.sortie || 0);
    var bar = el("div", "modes-barre");
    ["entree", "mixte", "sortie"].forEach(function (k) {
      if (!m[k]) return;
      var i = el("i");
      i.style.width = (100 * m[k] / tot) + "%";
      i.style.background = COUL[k];
      i.title = MODE_MOT[k] + " : " + dureeTxt(m[k]);
      bar.appendChild(i);
    });
    c.appendChild(bar);
    return c;
  }
  function dureeTxt(min) {
    var h = Math.floor(min / 60), m = min % 60;
    return h ? (h + " h" + (m ? String(m).padStart(2, "0") : "")) : (m + " min");
  }

  // ------------------------------------------------------------------------
  // Analyse complete d'UNE porte (vue-porte) : depuis Direct / Rejeu (bouton
  // "Analyse complete") ou depuis le Bilan (nom de la porte). Etat a
  // l'instant (meme detail que la liste) + tout ce que les archives HSH
  // disent de la porte sur la periode (/api/portes/porte, portes_dossier.py).

  var FAM_COUL = { double_usage: "#e11d48", titre: "#f59e0b", reentree: "#7c3aed", ouverture: "#0891b2",
                   annulation: "#64748b", hors_ligne: "#0f766e", autre: "#94a3b8" };
  var SUPPORT_COURT = function (l) { return String(l || "").replace("Code-barres visible ", ""); };
  var focusSeq = 0;
  var focusInitial = false;

  function ouvrirFocus(porte, jour, a, origine) {
    focus = { porte: porte, jour: jour || null, a: a || null, origine: origine };
    $("vue-etat").hidden = true;
    $("vue-bilan").hidden = true;
    $("vue-porte").hidden = false;
    var u = new URL(location.href);
    u.searchParams.set("porte", porte); u.searchParams.set("focus", "1");
    history.replaceState(null, "", u.toString());
    remplirChoixFocus();
    rendreFocus();
    $("vue-porte").scrollTop = 0;
  }

  function fermerFocus(silencieux) {
    if (!focus) return;
    var origine = focus.origine;
    focus = null;
    graphesFocus.forEach(function (g) { g.destroy(); });
    graphesFocus = [];
    $("vue-porte").hidden = true;
    var u = new URL(location.href);
    u.searchParams.delete("focus");
    history.replaceState(null, "", u.toString());
    if (silencieux) return;
    if (origine === "bilan") { $("vue-bilan").hidden = false; }
    else { $("vue-etat").hidden = false; if (dernier) rendreEtat(dernier); }
  }
  $("pf-retour").addEventListener("click", function () { fermerFocus(false); });

  function joursEdition() {
    if (bilanData && bilanData.jours && bilanData.jours.length) return bilanData.jours.map(function (j) { return j.jour; });
    var out = [];
    if (dernier && dernier.fenetre) {
      var a = new Date(dernier.fenetre[0] + "T12:00"), b = new Date(dernier.fenetre[1] + "T12:00");
      for (var t = a; t <= b; t = new Date(t.getTime() + 86400000)) {
        out.push(t.getFullYear() + "-" + String(t.getMonth() + 1).padStart(2, "0") + "-" + String(t.getDate()).padStart(2, "0"));
      }
    }
    return out;
  }

  function remplirChoixFocus() {
    var sel = $("pf-porte");
    vider(sel);
    var noms = [];
    if (focus.origine === "bilan" && bilanData) {
      bilanData.jours.forEach(function (j) { j.portes.forEach(function (p) { if (noms.indexOf(p.porte) < 0) noms.push(p.porte); }); });
    } else if (dernier && dernier.portes) {
      noms = dernier.portes.map(function (p) { return p.porte; });
    }
    if (noms.indexOf(focus.porte) < 0) noms.unshift(focus.porte);
    noms.sort().forEach(function (n) { var o = el("option", null, n); o.value = n; sel.appendChild(o); });
    sel.value = focus.porte;

    var per = $("pf-periodes");
    vider(per);
    var bt = function (lib, jour) {
      var b = el("button", focus.jour === jour ? "actif" : null, lib);
      b.type = "button";
      b.addEventListener("click", function () {
        focus.jour = jour;
        // Un autre jour que celui du rejeu : la journee entiere
        if (!dernier || jour !== dernier.jour) focus.a = null;
        remplirChoixFocus(); rendreFocus();
      });
      per.appendChild(b);
    };
    joursEdition().forEach(function (j) {
      bt(new Date(j + "T12:00").toLocaleDateString("fr-FR", { weekday: "short", day: "numeric", month: "short" }), j);
    });
    bt("Toute l'édition", null);
  }
  $("pf-porte").addEventListener("change", function () { focus.porte = this.value; rendreFocus(); });

  function rendreFocus() {
    if (!focus) return;
    var h = $("pf-contenu");
    vider(h);
    graphesFocus.forEach(function (g) { g.destroy(); });
    graphesFocus = [];
    // Etat a l'instant : seulement pour le jour affiche en Direct / Rejeu
    var p = focus.origine !== "bilan" && dernier && dernier.disponible && focus.jour === dernier.jour &&
      dernier.portes.filter(function (x) { return x.porte === focus.porte; })[0];
    if (p) {
      var c = el("div", "pf-carte");
      var d = el("div", "detail");
      c.appendChild(d); h.appendChild(c);
      rendreDetail(p, dernier, d);
    }
    var zone = el("div");
    zone.appendChild(el("p", "note", "Chargement des archives du contrôle d'accès..."));
    h.appendChild(zone);
    var seq = ++focusSeq;
    var q = { event: EVENT, year: YEAR, porte: focus.porte, jour: focus.jour };
    if (focus.jour && focus.a) q.a = focus.a;
    fetch("/api/portes/porte?" + qs(q), { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (dd) { if (seq === focusSeq && focus) rendreDossier(zone, dd); })
      .catch(function (e) { console.error("[Portes focus]", e); vider(zone); zone.appendChild(el("p", "note", "Archives indisponibles.")); });
  }

  function carte(titre, aide) {
    var c = el("div", "pf-carte");
    var s = section(titre, aide);
    s.style.marginTop = "0";
    c.appendChild(s);
    return { carte: c, corps: s };
  }

  function rendreDossier(zone, dd) {
    vider(zone);
    if (!dd.disponible) { zone.appendChild(el("div", "pf-carte note", dd.motif || "Aucune donnée.")); return; }
    var t = dd.totaux, per = dd.periode;
    var titre = per.edition ? "toute l'édition"
      : new Date(per.jour + "T12:00").toLocaleDateString("fr-FR", { weekday: "long", day: "numeric", month: "long" }) +
        (focus.a ? " jusqu'à " + focus.a : "");

    var k = carte("Contrôle d'accès · " + dd.porte, titre + " · passages valides, scans refusés et statuts particuliers des archives HSH");
    var g = el("div", "d-kpis");
    g.appendChild(dkpi("Entrées", fmt(t.entrees), null,
      [t.entrees_vehicules ? fmt(t.entrees_vehicules) + " véhicules" : "", t.entrees_enfants ? "dont " + fmt(t.entrees_enfants) + " enfants" : ""].filter(Boolean).join(" · ") || "passages valides"));
    g.appendChild(dkpi("Sorties", fmt(t.sorties), null, "borne basse à l'exode"));
    g.appendChild(dkpi("Scans refusés", fmt(t.refus), null, (t.refus_pct === null ? "--" : t.refus_pct + " %") + " des scans",
      t.refus_pct >= 10 ? NIVEAU_COUL.vigilance : null));
    g.appendChild(dkpi("Doubles scans", fmt(t.double_usage), null, "refusés (anti-double usage)", t.double_usage ? FAM_COUL.double_usage : null));
    g.appendChild(dkpi("Entrées sans sortie", fmt(t.reentrees), null,
      (t.reentrees_pct === null ? "--" : t.reentrees_pct + " %") + " des entrées, acceptées", t.reentrees ? FAM_COUL.reentree : null));
    g.appendChild(dkpi("Titres refusés", fmt(t.titres_refuses), null, "inconnus, non autorisés, expirés"));
    g.appendChild(dkpi("Ouvertures manuelles", fmt(t.ouvertures), null, t.forces ? "dont " + t.forces + " passages forcés" : "aucun passage forcé",
      t.forces ? NIVEAU_COUL.saturation : null));
    g.appendChild(dkpi("Libérations annulées", fmt(t.annulations), null, "ouvert puis refermé sans passage"));
    g.appendChild(dkpi("Hors ligne", fmt(t.valides_hors_ligne + t.checkpoint_hors_ligne), null, "validés par le lecteur sans le serveur",
      (t.valides_hors_ligne + t.checkpoint_hors_ligne) ? NIVEAU_COUL.vigilance : null));
    g.appendChild(dkpi("Appareils", String(t.appareils), null, "ayant validé un passage"));
    k.corps.appendChild(g);
    zone.appendChild(k.carte);

    // Familles de statuts, avec leur explication
    if (dd.familles.length) {
      var f = carte("Ce qui ne passe pas (ou passe autrement)", "part de tous les scans de la porte");
      var fb = el("div", "pf-fam");
      dd.familles.forEach(function (x) {
        var c = el("div", "f");
        c.style.borderLeftColor = FAM_COUL[x.cle] || "#94a3b8";
        var l = el("div", "l");
        l.appendChild(el("b", null, fmt(x.n) + " "));
        l.appendChild(document.createTextNode(x.libelle + (x.pct_scans !== null ? " · " + x.pct_scans + " %" : "")));
        c.appendChild(l);
        c.appendChild(el("div", "a", x.aide));
        fb.appendChild(c);
      });
      f.corps.appendChild(fb);
      zone.appendChild(f.carte);
    }

    // Graphes horaires
    var gr = el("div", "pf-grille");
    var c1 = carte("Passages et taux de refus par heure", "entrées vers le haut, sorties vers le bas, taux de refus (axe de droite)");
    var b1 = el("div", "graphe petit"), cv1 = el("canvas"); b1.appendChild(cv1); c1.corps.appendChild(b1);
    var c2 = carte("Refus et statuts particuliers par heure", "nombre de transactions par famille");
    var b2 = el("div", "graphe petit"), cv2 = el("canvas"); b2.appendChild(cv2); c2.corps.appendChild(b2);
    gr.appendChild(c1.carte); gr.appendChild(c2.carte);
    zone.appendChild(gr);

    // Appareils
    var ca = carte("Appareils", "passages valides et statuts par lecteur (tripodes TRI-, PDA)");
    var ta = el("table", "mini");
    var th = el("tr");
    ["Appareil", "Type", "Actif", "Entrées", "Sorties", "Refus", "Taux", "Doubles", "Titres", "Sans sortie", "Ouvertures", "Annulées", "Hors ligne"]
      .forEach(function (x) { th.appendChild(el("th", null, x)); });
    ta.appendChild(th);
    dd.appareils.forEach(function (a) {
      var tr = el("tr");
      tr.appendChild(el("td", null, a.nom));
      tr.appendChild(el("td", null, a.type));
      tr.appendChild(el("td", null, a.debut ? a.debut + "-" + a.fin : "--"));
      [a.e, a.s, a.refus].forEach(function (v) { tr.appendChild(el("td", "num", v ? fmt(v) : "--")); });
      var tx = el("td", "num", a.refus_pct === null ? "--" : a.refus_pct + " %");
      if (a.refus_pct >= 10) tx.style.color = NIVEAU_COUL.vigilance;
      tr.appendChild(tx);
      [a.double_usage, a.titre, a.reentree, a.ouverture, a.annulation, a.hors_ligne].forEach(function (v) {
        tr.appendChild(el("td", "num", v ? fmt(v) : "--"));
      });
      ta.appendChild(tr);
    });
    ca.corps.appendChild(ta);
    zone.appendChild(ca.carte);

    var gr2 = el("div", "pf-grille");
    // Motifs exacts
    var cm = carte("Motifs HSH", "chaque statut non nul, par sens");
    var tm = el("table", "mini");
    var th2 = el("tr");
    ["Motif", "Famille", "Total", "Entrée", "Sortie", "% scans"].forEach(function (x) { th2.appendChild(el("th", null, x)); });
    tm.appendChild(th2);
    dd.statuts.forEach(function (r) {
      var tr = el("tr");
      tr.appendChild(el("td", null, r.libelle + " (" + r.status + ")"));
      var fam = el("td");
      var pf = el("span", "sens", (dd.familles.filter(function (x) { return x.cle === r.famille; })[0] || {}).libelle || r.famille);
      pf.style.background = (FAM_COUL[r.famille] || "#94a3b8") + "22"; pf.style.color = FAM_COUL[r.famille] || "#475569";
      fam.appendChild(pf); tr.appendChild(fam);
      tr.appendChild(el("td", "num", fmt(r.n)));
      tr.appendChild(el("td", "num", r.e ? fmt(r.e) : "--"));
      tr.appendChild(el("td", "num", r.s ? fmt(r.s) : "--"));
      tr.appendChild(el("td", "num", t.scans ? (Math.round(1000 * r.n / t.scans) / 10) + " %" : "--"));
      tm.appendChild(tr);
    });
    if (!dd.statuts.length) cm.corps.appendChild(el("p", "note", "Aucun statut particulier."));
    else cm.corps.appendChild(tm);
    gr2.appendChild(cm.carte);

    // Titres refuses plusieurs fois
    var ct = carte("Billets refusés plusieurs fois", "3 refus ou plus sur la période : billet partagé, re-scan insistant, carte de contrôle");
    if (!dd.titres_recurrents.length) ct.corps.appendChild(el("p", "note", "Aucun billet refusé 3 fois ou plus."));
    else {
      var tt = el("table", "mini");
      var th3 = el("tr");
      ["Billet", "Refus", "Motif principal", "Appareils", "Premier", "Dernier"].forEach(function (x) { th3.appendChild(el("th", null, x)); });
      tt.appendChild(th3);
      dd.titres_recurrents.forEach(function (r) {
        var tr = el("tr");
        tr.appendChild(el("td", "mono", r.titre));
        tr.appendChild(el("td", "num", fmt(r.n)));
        tr.appendChild(el("td", null, r.motif));
        tr.appendChild(el("td", null, r.appareils.length > 3 ? r.appareils.length + " appareils" : r.appareils.join(", ")));
        tr.appendChild(el("td", null, quand(r.premier, per.edition)));
        tr.appendChild(el("td", null, quand(r.dernier, per.edition)));
        tt.appendChild(tr);
      });
      ct.corps.appendChild(tt);
    }
    gr2.appendChild(ct.carte);
    zone.appendChild(gr2);

    // Supports et profils des refus
    var gr3 = el("div", "pf-grille");
    gr3.appendChild(listeSimple("Refus par support", "type de billet présenté (code-barres, RFID)", dd.supports, SUPPORT_COURT, t.refus));
    gr3.appendChild(listeSimple("Refus par profil", "type de passage déclaré par le lecteur", dd.types, null, t.refus));
    zone.appendChild(gr3);

    zone.appendChild(el("p", "note", "Passages valides = statut 0, plus 107 (checkpoint hors ligne) et 133 (entrée sans sortie préalable) que le compteur HSH compte aussi. " +
      "Taux de refus = doubles scans + titres refusés, sur l'ensemble des scans. Les ouvertures manuelles et libérations annulées ne sont pas des refus de billet. " +
      "Sources : archives des transactions (5 min) et de chaque scan en statut non nul du contrôle d'accès live."));

    dessinerFocus(cv1, cv2, dd);
  }

  function quand(iso, avecJour) {
    if (!iso) return "--";
    var d = new Date(iso);
    var hm = String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0");
    return avecJour ? d.toLocaleDateString("fr-FR", { day: "numeric", month: "short" }) + " " + hm : hm;
  }

  function listeSimple(titre, aide, rows, court, total) {
    var c = carte(titre, aide);
    if (!rows.length) { c.corps.appendChild(el("p", "note", "Aucun refus.")); return c.carte; }
    var t = el("table", "mini");
    rows.forEach(function (r) {
      var tr = el("tr");
      tr.appendChild(el("td", null, court ? court(r.libelle) : r.libelle));
      tr.appendChild(el("td", "num", fmt(r.n)));
      tr.appendChild(el("td", "num", total ? Math.round(100 * r.n / total) + " %" : "--"));
      t.appendChild(tr);
    });
    c.corps.appendChild(t);
    return c.carte;
  }

  function dessinerFocus(cv1, cv2, dd) {
    if (typeof Chart === "undefined" || !dd.serie.length) return;
    var lab = dd.serie.map(function (x) { return x.t; });
    var axeX = { stacked: true, ticks: { color: "#607286", maxTicksLimit: 14, autoSkip: true }, grid: { color: "rgba(96,114,134,0.10)" } };
    var leg = { labels: { color: "#1f2d3d", boxWidth: 12, font: { size: 11, family: "Outfit" } } };
    graphesFocus.push(new Chart(cv1.getContext("2d"), {
      data: { labels: lab, datasets: [
        { type: "bar", label: "Entrées", data: dd.serie.map(function (x) { return x.e; }), backgroundColor: "rgba(37,99,235,0.70)", stack: "m", yAxisID: "y" },
        { type: "bar", label: "Sorties", data: dd.serie.map(function (x) { return -x.s; }), backgroundColor: "rgba(234,88,12,0.70)", stack: "m", yAxisID: "y" },
        { type: "line", label: "Taux de refus %", data: dd.serie.map(function (x) { return x.refus_pct; }), borderColor: FAM_COUL.double_usage,
          borderWidth: 2, pointRadius: 0, yAxisID: "y1", spanGaps: true }
      ] },
      options: {
        responsive: true, maintainAspectRatio: false, animation: false, interaction: { mode: "index", intersect: false },
        scales: {
          x: axeX,
          y: { ticks: { color: "#607286", callback: function (v) { return Math.abs(v).toLocaleString("fr-FR"); } },
               grid: { color: function (c) { return c.tick.value === 0 ? "rgba(31,45,61,0.45)" : "rgba(96,114,134,0.10)"; } } },
          y1: { position: "right", min: 0, suggestedMax: 10, ticks: { color: FAM_COUL.double_usage, callback: function (v) { return v + " %"; } }, grid: { display: false } }
        },
        plugins: { legend: leg, tooltip: { callbacks: { label: function (c) {
          return c.dataset.label + " : " + (c.raw === null ? "--" : Math.abs(c.raw).toLocaleString("fr-FR"));
        } } } }
      }
    }));
    var fams = ["double_usage", "titre", "reentree", "ouverture", "annulation", "hors_ligne"];
    var noms = {};
    dd.familles.forEach(function (x) { noms[x.cle] = x.libelle; });
    var ds = fams.filter(function (k) { return dd.serie.some(function (x) { return x[k]; }); }).map(function (k) {
      return { type: "bar", label: noms[k] || k, data: dd.serie.map(function (x) { return x[k]; }), backgroundColor: FAM_COUL[k], stack: "f" };
    });
    graphesFocus.push(new Chart(cv2.getContext("2d"), {
      data: { labels: lab, datasets: ds },
      options: {
        responsive: true, maintainAspectRatio: false, animation: false, interaction: { mode: "index", intersect: false },
        scales: { x: axeX, y: { stacked: true, ticks: { color: "#607286" }, grid: { color: "rgba(96,114,134,0.10)" } } },
        plugins: { legend: leg }
      }
    }));
  }

  // ------------------------------------------------------------------------
  // Vues

  function changerVue(v) {
    fermerFocus(true);
    vue = v;
    document.querySelectorAll(".vue").forEach(function (b) { b.classList.toggle("actif", b.dataset.vue === v); });
    $("rejeu-ctrl").hidden = v !== "rejeu";
    $("vue-etat").hidden = v === "bilan";
    $("vue-bilan").hidden = v !== "bilan";
    clearInterval(minuteur);
    if (v === "bilan") {
      chargerBilan();
      return;
    }
    if (v === "direct") vider($("rejeu-jour"));
    charger();
    if (v === "direct") minuteur = setInterval(charger, PERIODE_MS);
  }
  document.querySelectorAll(".vue").forEach(function (b) {
    b.addEventListener("click", function () { changerVue(b.dataset.vue); });
  });

  // Lien direct : ?porte=...&jour=AAAA-MM-JJ&a=HH:MM ouvre l'analyse d'une
  // porte, en rejeu si un jour est donne.
  var url = new URLSearchParams(location.search);
  remplirEditions();
  if (url.get("porte")) choisie = url.get("porte");
  // ?focus=1 : ouvre l'analyse complete de ?porte= des le premier chargement
  focusInitial = url.get("focus") === "1" && !!choisie;
  if (url.get("vue") === "bilan") {
    changerVue("bilan");
  } else if (url.get("jour")) {
    var o = el("option", null, url.get("jour"));
    o.value = url.get("jour");
    $("rejeu-jour").appendChild(o);
    var hm = (url.get("a") || "12:00").split(":");
    $("rejeu-heure").value = Math.min(95, Math.floor((+hm[0] * 60 + +hm[1]) / 15));
    $("rejeu-h").textContent = heureDe(+$("rejeu-heure").value);
    changerVue("rejeu");
  } else {
    changerVue("direct");
  }
})();
