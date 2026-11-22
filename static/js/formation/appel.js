/* Epreuve 2 : le 38 17 (maquette, RENDER.call). Appels et choix deja melanges
 * par le serveur, sans bareme. Chaque choix envoie {type: "reponse", question:
 * id de l'etape, choix}. Aucun retour juste / faux : « Réponse enregistrée ».
 * Pas de compte a rebours visible (spec 12). */
(function () {
  "use strict";
  var F = window.Formation;

  F.epreuves.appel = {
    intro: function (el) {
      F.entete("La ligne 38 17 sonne. Choisissez à chaque fois ce que vous dites à l'appelant.");
      el.innerHTML = "<div class=\"rules\"><h3>Méthode de l'appel entrant</h3><ol>" +
        "<li><b>Où ?</b> Le lieu précis, d'abord.</li><li><b>Quoi ?</b> Ce qui se passe.</li>" +
        "<li><b>État</b> : la personne répond-elle, respire-t-elle ?</li>" +
        "<li><b>Engager</b> le bon moyen, et rester en ligne si nécessaire.</li></ol></div>";
    },
    jouer: function (el, enonce, coef, fini) {
      var g = F.groupe(), appels = enonce.appels || [], c = 0;
      var appel = function () {
        if (c >= appels.length) { fini(); return; }
        var a = appels[c], s = 0;
        F.entete("Appel " + (c + 1) + " / " + appels.length + ".");
        el.innerHTML = "<div class=\"phone\" aria-live=\"polite\">● Appel entrant · ligne 38 17</div>" +
          "<div class=\"call\" aria-live=\"polite\"></div><div class=\"choices une\"></div>" +
          "<p class=\"enreg\" role=\"status\"></p>";
        var conv = el.querySelector(".call"), opts = el.querySelector(".choices"), enreg = el.querySelector(".enreg");
        F.son.bip(660, 0.2, "sine", 0.06);
        g.later(function () { F.son.bip(660, 0.2, "sine", 0.06); }, 350);
        var bulle = function (texte, qui) {
          var d = document.createElement("div");
          d.className = "bub " + qui;
          var sm = document.createElement("small");
          sm.textContent = qui === "them" ? "Appelant" : "Vous, PC Org";
          d.appendChild(sm);
          d.appendChild(document.createTextNode(texte));
          conv.appendChild(d);
        };
        var etape = function () {
          if (s >= a.etapes.length) { c++; g.later(appel, 900); return; }
          var e = a.etapes[s];
          bulle(e.appelant, "them");
          opts.innerHTML = "";
          enreg.textContent = "";
          var repondu = false;
          e.choix.forEach(function (ch) {
            var b = document.createElement("button");
            b.type = "button";
            b.className = "choice";
            b.textContent = ch.texte;
            b.onclick = function () {
              if (repondu) return;
              repondu = true;
              F.action("appel", {type: "reponse", question: e.id, choix: ch.id});
              bulle(ch.texte, "me");
              opts.innerHTML = "";
              enreg.textContent = "Réponse enregistrée.";
              s++;
              g.later(etape, 700);
            };
            opts.appendChild(b);
          });
          F.focus(opts.firstChild);
        };
        g.later(etape, 500);
      };
      appel();
    }
  };
})();
