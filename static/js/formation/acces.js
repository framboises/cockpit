/* Epreuve 5 : controle d'acces (maquette, RENDER.gate). Badges dans l'ordre fixe
 * de la banque. A l'affichage : {type: "badge_affiche", badge} ; decision :
 * {type: "decision", badge, decision: 1|0}. 5 s x coef par badge, puis « pas de
 * decision » (rien n'est envoye) ; badge suivant 450 ms apres. Retour immediat
 * garde (bonne / mauvaise decision), comme dans la maquette. */
(function () {
  "use strict";
  var F = window.Formation;
  var DELAI = 5000, TRANSITION = 450;
  var CLASSES = {ROUGE: "c-rouge", OR: "c-or"};

  function regle(couleurs, heure) {
    var cs = (couleurs || []).map(function (c) {
      return "<b class=\"" + (CLASSES[c] || "") + "\">" + F.esc(c) + "</b>";
    });
    return "<div class=\"rules\"><h3>Règle d'accès au Grand Paddock, cette nuit</h3><ul>" +
      "<li>Entrée autorisée avec un badge " + cs.join(" ou ") + ".</li>" +
      "<li>Et seulement si le badge est encore valable : il est <b>" + F.esc(heure) + "</b>.</li>" +
      "<li>Tous les autres : refus poli.</li></ul></div>";
  }

  F.epreuves.acces = {
    intro: function (el, enonce, coef) {
      F.entete("Des personnes se présentent à l'entrée du Grand Paddock. Laissez entrer ou refusez, en " +
               F.secondes(DELAI / 1000 * coef) + " secondes chacune.");
      // Regle de la nuit (couleurs, heure) lue avant de commencer ; les badges ne partent qu'avec l'enonce.
      el.innerHTML = "<div class=\"rules\"><h3>Règles</h3><ul>" +
        "<li>Pour chaque personne : <b>Laisser entrer</b> ou <b>Refuser</b>, en " +
        F.secondes(DELAI / 1000 * coef) + " secondes au plus.</li>" +
        "<li>Sans décision dans le temps, la personne suivante se présente.</li>" +
        "<li>La règle ci-dessous reste affichée à côté de chaque badge pendant l'épreuve.</li></ul></div>" +
        "<div class=\"regle-acces\"><p class=\"enreg\">Chargement de la règle d'accès…</p></div>";
      var zone = el.querySelector(".regle-acces");
      F.api("/api/entrainement/acces").then(function (r) {
        var d = r.data;
        if (r.status === 200 && d && d.ok && d.contenu) zone.innerHTML = regle(d.contenu.couleurs, d.contenu.heure);
        else if (r.status !== 401) zone.innerHTML = "<p class=\"enreg\">La règle s'affichera avec le premier badge.</p>";
      });
    },
    jouer: function (el, enonce, coef, fini) {
      var badges = enonce.badges || [], k = 0, delai = DELAI * coef;
      var texteRegle = regle(enonce.couleurs, enonce.heure);
      var suivant = function () {
        if (k >= badges.length) { fini(); return; }
        var b = badges[k], fait = false, g = F.groupe();
        F.entete("Personne " + (k + 1) + " / " + badges.length + ". Il est " + (enonce.heure || "") + ".", true);
        el.innerHTML = "<div class=\"gate\"><div class=\"badge\" tabindex=\"-1\" aria-live=\"polite\"><div class=\"band\"></div><div class=\"num\"></div>" +
          "<div class=\"meta\">Valable jusqu'à <b style=\"color:var(--fg)\" class=\"until\"></b></div></div>" +
          "<div>" + texteRegle + "<div class=\"gatebtns\">" +
          "<button class=\"choice ok\" type=\"button\">Laisser entrer</button>" +
          "<button class=\"choice no\" type=\"button\">Refuser</button></div>" +
          "<p class=\"enreg\" role=\"status\"></p></div></div>";
        var band = el.querySelector(".band");
        band.style.background = /^#[0-9A-Fa-f]{6}$/.test(b.bg || "") ? b.bg : "#444";
        band.textContent = "BADGE " + b.col;
        el.querySelector(".num").textContent = b.num;
        el.querySelector(".until").textContent = b.until;
        var oui = el.querySelector(".gatebtns .ok"), non = el.querySelector(".gatebtns .no");
        var enreg = el.querySelector(".enreg");
        var montre = performance.now();
        F.action("acces", {type: "badge_affiche", badge: b.id});
        var decider = function (v) {
          if (fait) return;
          fait = true;
          g.stop();
          oui.disabled = true;
          non.disabled = true;
          if (v === null) {
            enreg.textContent = "Temps écoulé : pas de décision.";
            F.son.bip(200, 0.15, "sawtooth");
          } else {
            F.action("acces", {type: "decision", badge: b.id, decision: v});
            var juste = v === b.ok;
            (v ? oui : non).classList.add(juste ? "right" : "wrong");
            if (juste) F.son.bip(1300, 0.05); else F.son.bip(200, 0.15, "sawtooth");
          }
          F.groupe().later(function () { k++; suivant(); }, TRANSITION);
        };
        // Focus sur le badge, jamais sur un bouton : un appui involontaire sur Espace / Entree ne decide rien.
        F.focus(el.querySelector(".badge"));
        oui.onclick = function () { decider(1); };
        non.onclick = function () { decider(0); };
        F.chrono(delai, montre, g);
        g.later(function () { decider(null); }, delai);
      };
      suivant();
    }
  };
})();
