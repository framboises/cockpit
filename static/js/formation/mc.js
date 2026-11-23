/* Epreuve 3 : la main courante (maquette, RENDER.mc). Fiches melangees par le
 * serveur, sans corrige. « Enregistrer la fiche » envoie {type: "reponse",
 * question: id de la fiche, cat, zone, heure, moyen}. Aucune coloration juste /
 * faux : « Réponse enregistrée ». Pas de compte a rebours visible (spec 12). */
(function () {
  "use strict";
  var F = window.Formation;
  var esc = function (s) { return F.esc(s); };

  function categories(liste) {
    return "<ul>" + (liste || []).map(function (c) {
      return "<li><b>" + esc(c.nom) + "</b> : " + esc(c.description) + "</li>";
    }).join("") + "</ul>";
  }

  function options(liste) {
    return "<option value=\"\">Choisir…</option>" + (liste || []).map(function (v) {
      return "<option value=\"" + esc(v) + "\">" + esc(v) + "</option>";
    }).join("");
  }

  F.epreuves.mc = {
    intro: function (el) {
      F.entete("Des messages arrivent. Pour chacun, remplissez la fiche de main courante correspondante.");
      el.innerHTML = "<div class=\"rules\"><h3>La fiche de main courante</h3><ul>" +
        "<li>Pour chaque message : la catégorie, le lieu, l'heure (hh:mm) et le moyen qui parle ou intervient.</li>" +
        "<li>Les définitions des catégories sont affichées avec chaque message.</li>" +
        "<li>Prenez le temps de lire le message en entier avant d'enregistrer la fiche.</li></ul></div>";
    },
    jouer: function (el, enonce, coef, fini) {
      var g = F.groupe(), fiches = enonce.fiches || [], k = 0;
      var noms = (enonce.categories || []).map(function (c) { return c.nom; });
      var cats = categories(enonce.categories);
      var dessiner = function () {
        if (k >= fiches.length) { fini(); return; }
        var m = fiches[k], envoye = false, averti = false;
        F.entete("Message " + (k + 1) + " / " + fiches.length + ".");
        el.innerHTML = "<div class=\"msg\"><span class=\"from\">" + esc(m.de) + " → PC Org Sud-Est</span>" +
          "<span class=\"ty typing\"></span></div>" +
          "<form class=\"mc\" autocomplete=\"off\" novalidate>" +
          "<label>Catégorie<select name=\"cat\">" + options(noms) + "</select></label>" +
          "<label>Lieu<select name=\"zone\">" + options(enonce.zones) + "</select></label>" +
          "<label>Heure (hh:mm)<input name=\"heure\" inputmode=\"numeric\" placeholder=\"00:00\" maxlength=\"5\"></label>" +
          "<label>Moyen<select name=\"moyen\">" + options(enonce.moyens) + "</select></label></form>" +
          "<details class=\"rules\" open><summary>Catégories de la main courante</summary>" + cats + "</details>" +
          "<div class=\"row actions\"><button class=\"btn\" type=\"button\">Enregistrer la fiche</button></div>" +
          "<p class=\"enreg\" role=\"status\"></p>";
        var ty = el.querySelector(".ty"), form = el.querySelector("form");
        var bouton = el.querySelector(".actions .btn"), enreg = el.querySelector(".enreg");
        if (F.reduit) {
          ty.textContent = m.texte;
          ty.classList.remove("typing");
        } else {
          var n = 0;
          var iv = g.every(function () {
            n += 2;
            ty.textContent = m.texte.slice(0, n);
            if (n >= m.texte.length) { clearInterval(iv); ty.classList.remove("typing"); }
          }, 28);
        }
        var heure = form.elements.heure;
        heure.oninput = function () {
          var v = heure.value.replace(/\D/g, "").slice(0, 4);
          if (v.length > 2) v = v.slice(0, 2) + ":" + v.slice(2);
          heure.value = v;
        };
        form.onsubmit = function (e) { e.preventDefault(); };
        F.focus(form.elements.cat);
        bouton.onclick = function () {
          if (envoye) return;
          var d = {type: "reponse", question: m.id};
          var vide = false;
          ["cat", "zone", "heure", "moyen"].forEach(function (f) {
            d[f] = form.elements[f].value.trim();
            if (!d[f]) vide = true;
          });
          if (vide && !averti) {
            averti = true;
            enreg.textContent = "Tous les champs ne sont pas remplis. Cliquez à nouveau sur « Enregistrer la fiche » " +
              "pour l'enregistrer quand même.";
            return;
          }
          envoye = true;
          F.action("mc", d);
          bouton.disabled = true;
          Array.prototype.forEach.call(form.elements, function (x) { x.disabled = true; });
          enreg.textContent = "Réponse enregistrée.";
          g.later(function () { k++; dessiner(); }, 900);
        };
      };
      dessiner();
    }
  };
})();
