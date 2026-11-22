/* Epreuve 7 : le pic du matin, mises en situation (maquette, RENDER.judge).
 * Situations et choix melanges par le serveur, sans bareme. Chaque choix envoie
 * {type: "reponse", question, choix}. Aucune coloration : « Réponse enregistrée ».
 * Pas de compte a rebours visible (spec 12). */
(function () {
  "use strict";
  var F = window.Formation;

  F.epreuves.situations = {
    intro: function (el) {
      F.entete("Le jour se lève, les portes rouvrent, le standard repart. Trois situations à trancher.");
      el.innerHTML = "<div class=\"rules\"><h3>Règles</h3><ul><li>Chaque situation rappelle la consigne utile.</li>" +
        "<li>Choisissez la réponse qui vous semble la meilleure.</li></ul></div>";
    },
    jouer: function (el, enonce, coef, fini) {
      var g = F.groupe(), qs = enonce.questions || [], i = 0;
      var dessiner = function () {
        if (i >= qs.length) { fini(); return; }
        var s = qs[i], repondu = false;
        F.entete("Situation " + (i + 1) + " / " + qs.length + ".");
        el.innerHTML = "<div class=\"msg\"></div><div class=\"choices une\"></div><p class=\"enreg\" role=\"status\"></p>";
        el.querySelector(".msg").textContent = s.texte;
        var zone = el.querySelector(".choices"), enreg = el.querySelector(".enreg");
        (s.choix || []).forEach(function (c) {
          var b = document.createElement("button");
          b.type = "button";
          b.className = "choice";
          b.textContent = c.texte;
          b.onclick = function () {
            if (repondu) return;
            repondu = true;
            F.action("situations", {type: "reponse", question: s.id, choix: c.id});
            b.classList.add("choisi");
            Array.prototype.forEach.call(zone.children, function (x) { x.disabled = true; });
            enreg.textContent = "Réponse enregistrée.";
            g.later(function () { i++; dessiner(); }, 600);
          };
          zone.appendChild(b);
        });
      };
      dessiner();
    }
  };
})();
