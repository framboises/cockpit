/* Epreuve 6 : la releve, logique (maquette, RENDER.logic). Questions et choix
 * melanges par le serveur, sans la bonne reponse. Chaque choix envoie
 * {type: "reponse", question, choix}. Compte a rebours visible de 4 min x coef ;
 * a zero, l'epreuve se termine. Aucune coloration : « Réponse enregistrée ». */
(function () {
  "use strict";
  var F = window.Formation;
  var DUREE = 240000;

  F.epreuves.logique = {
    intro: function (el, enonce, coef) {
      F.entete("Pendant la relève de 3 h, cinq questions de logique. Toutes les données sont dans l'énoncé.");
      el.innerHTML = "<div class=\"rules\"><h3>Règles</h3><ul><li>Une seule bonne réponse par question.</li>" +
        "<li>" + F.duree(DUREE * coef) + " pour l'ensemble. Le temps démarre quand vous cliquez sur " +
        "« Commencer l'épreuve ».</li><li>Une réponse donnée est définitive.</li></ul></div>";
    },
    jouer: function (el, enonce, coef, fini) {
      var g = F.groupe(), qs = enonce.questions || [], i = 0;
      var dessiner = function () {
        if (i >= qs.length) { fini(); return; }
        var q = qs[i], repondu = false;
        F.entete("Question " + (i + 1) + " / " + qs.length + ".", true);
        var barre = qs.map(function (_, k) { return "<i class=\"" + (k < i ? "on" : "") + "\"></i>"; }).join("");
        el.innerHTML = "<div class=\"progress\" aria-hidden=\"true\">" + barre + "</div>" +
          "<div class=\"msg\"></div><div class=\"choices\"></div><p class=\"enreg\" role=\"status\"></p>";
        el.querySelector(".msg").textContent = q.texte;
        var zone = el.querySelector(".choices"), enreg = el.querySelector(".enreg");
        (q.choix || []).forEach(function (c) {
          var b = document.createElement("button");
          b.type = "button";
          b.className = "choice";
          b.textContent = c.texte;
          b.onclick = function () {
            if (repondu) return;
            repondu = true;
            F.action("logique", {type: "reponse", question: q.id, choix: c.id});
            b.classList.add("choisi");
            Array.prototype.forEach.call(zone.children, function (x) { x.disabled = true; });
            enreg.textContent = "Réponse enregistrée.";
            g.later(function () { i++; dessiner(); }, 500);
          };
          zone.appendChild(b);
        });
      };
      F.chrono(DUREE * coef, F.t0, g, fini);
      dessiner();
    }
  };
})();
