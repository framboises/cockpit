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
  var EVENT = document.body.dataset.event || "";
  var YEAR = document.body.dataset.year || "";

  var COUL = { entree: "#3b82f6", sortie: "#f97316", mixte: "#a78bfa", ferme: "#475569" };
  var NIVEAU_COUL = { saturation: "#ef4444", saturation_prevue: "#f97316", vigilance: "#eab308", normal: "#22c55e" };
  var NIVEAU_MOT = { saturation: "Saturation", saturation_prevue: "Saturation prévue", vigilance: "Vigilance" };
  var MODE_MOT = { entree: "Entrée", sortie: "Sortie", mixte: "Mixte" };

  var vue = "direct";
  var dernier = null;       // derniere reponse /api/portes/etat
  var choisie = null;       // nom de la porte analysee
  var graphe = null;
  var minuteur = null;
  var bilanData = null, bilanJour = null;

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
    if (c === null || c === undefined) return "#64748b";
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
        if (!EVENT && d.event) { EVENT = d.event; YEAR = String(d.year); }
        $("edition").textContent = (d.event || "") + " " + (d.year || "");
        rendreEtat(d);
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
    if (!d.disponible) {
      vider($("kpis")); vider($("groupes"));
      $("groupes").appendChild(el("p", "note", d.motif || "Aucune donnée."));
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

  function rendreDetail(p, d) {
    $("detail-vide").hidden = true;
    var h = $("detail-contenu");
    h.hidden = false;
    vider(h);

    var tete = el("div", "d-tete");
    tete.appendChild(el("h2", null, p.porte));
    var m = p.fermee ? "ferme" : (p.mode || "ferme");
    tete.appendChild(el("span", "badge mode " + m, p.fermee ? "Fermée" : (MODE_MOT[p.mode] || "--")));
    if (NIVEAU_MOT[p.niveau]) tete.appendChild(el("span", "badge " + p.niveau, NIVEAU_MOT[p.niveau]));
    if (p.pda_renfort) tete.appendChild(el("span", "badge info", "PDA en renfort des tripodes"));
    if (p.uam && p.uam.length) tete.appendChild(el("span", "badge info", "UAM : " + p.uam.join(", ")));
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
      { type: "bar", label: "Entrées /h", data: e, backgroundColor: "rgba(59,130,246,0.75)", stack: "m", barPercentage: 1, categoryPercentage: 0.95 },
      { type: "bar", label: "Sorties /h", data: so, backgroundColor: "rgba(249,115,22,0.75)", stack: "m", barPercentage: 1, categoryPercentage: 0.95 },
      { type: "line", label: "N-1 entrées", data: n1e, borderColor: "#93c5fd", borderDash: [5, 4], borderWidth: 1.5, pointRadius: 0, spanGaps: false },
      { type: "line", label: "N-1 sorties", data: n1s, borderColor: "#fdba74", borderDash: [5, 4], borderWidth: 1.5, pointRadius: 0, spanGaps: false },
      { type: "line", label: "Capacité appareils entrée", data: capE, borderColor: "rgba(148,163,184,0.7)", borderWidth: 1, pointRadius: 0, stepped: true }
    ];
    if (n1t.some(function (x) { return x; })) {
      // Import horaire N-1 : passages tous sens confondus, impossible a
      // ranger en entrees ou en sorties.
      ds.push({ type: "line", label: "N-1 tous sens", data: n1t, borderColor: "#94a3b8", borderDash: [2, 3], borderWidth: 1.5, pointRadius: 0 });
    }
    if (cap.some(function (x) { return x; })) {
      ds.push({ type: "line", label: "Capacité sécurité", data: cap, borderColor: "#ef4444", borderWidth: 2, pointRadius: 0, stepped: true });
    }
    // Une serie vide (pas de N-1 par sens, pas de planning) encombrerait la legende.
    ds = ds.filter(function (x) { return x.data.some(function (v) { return v !== null && v !== undefined; }); });
    graphe = new Chart(cv.getContext("2d"), {
      data: { labels: lab, datasets: ds },
      options: {
        responsive: true, maintainAspectRatio: false, animation: false,
        interaction: { mode: "index", intersect: false },
        scales: {
          x: { stacked: true, ticks: { color: "#94a3b8", maxTicksLimit: 13 }, grid: { color: "rgba(148,163,184,0.08)" } },
          y: { ticks: { color: "#94a3b8", callback: function (v) { return Math.abs(v).toLocaleString("fr-FR"); } },
               grid: { color: function (c) { return c.tick.value === 0 ? "rgba(226,232,240,0.5)" : "rgba(148,163,184,0.08)"; } } }
        },
        plugins: {
          legend: { labels: { color: "#cbd5e1", boxWidth: 12, font: { size: 11 } } },
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
          ctx.strokeStyle = "rgba(255,255,255,0.6)"; ctx.setLineDash([3, 3]);
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
    var sel = $("bilan-edition");
    var ev = EVENT, yr = YEAR;
    if (sel.value) { var p = sel.value.split("|"); ev = p[0]; yr = p[1]; }
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
      })
      .catch(function (e) { console.error("[Portes bilan]", e); fraicheur(false, "bilan indisponible"); });
  }

  function remplirEditions() {
    fetch("/api/portes/editions", { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (rows) {
        var sel = $("bilan-edition");
        vider(sel);
        var courant = EVENT + "|" + YEAR, vu = false;
        rows.forEach(function (r) {
          var o = el("option", null, r.event + " " + r.year);
          o.value = r.event + "|" + r.year;
          if (o.value === courant) vu = true;
          sel.appendChild(o);
        });
        if (!vu && EVENT) {
          var o = el("option", null, EVENT + " " + YEAR);
          o.value = courant;
          sel.insertBefore(o, sel.firstChild);
        }
        sel.value = courant;
        chargerBilan();
      });
  }
  $("bilan-edition").addEventListener("change", chargerBilan);

  function rendreBilan() {
    var hj = $("bilan-jours"), t = $("bilan-table");
    vider(hj); vider(t);
    var b = bilanData;
    if (!b || !b.disponible) {
      $("bilan-note").textContent = (b && b.motif) || "Aucune donnée.";
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
      l.appendChild(el("td", "porte-nom", r.porte));
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
  // Vues

  function changerVue(v) {
    vue = v;
    document.querySelectorAll(".vue").forEach(function (b) { b.classList.toggle("actif", b.dataset.vue === v); });
    $("rejeu-ctrl").hidden = v !== "rejeu";
    $("bilan-ctrl").hidden = v !== "bilan";
    $("vue-etat").hidden = v === "bilan";
    $("vue-bilan").hidden = v !== "bilan";
    clearInterval(minuteur);
    if (v === "bilan") {
      if (!$("bilan-edition").options.length) remplirEditions(); else chargerBilan();
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
  if (url.get("porte")) choisie = url.get("porte");
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
