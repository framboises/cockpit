/* Epreuve 1 : le canal radio (maquette test_pcorg.html, RENDER.radio).
 * Horaires calcules sur le temps ecoule depuis le debut (comme correction.py) :
 * message k affiche de debut_k a fin_k, en ms de jeu x coef. Une prise envoie
 * {type: "prise", msg, t_ms}. Retour immediat garde (pris, pris a tort, manque).
 * Entrainement : 2 messages, rien n'est enregistre, rejouable. */
(function () {
  "use strict";
  var F = window.Formation;
  var PREMIER = 800, CADENCE = 4000, CADENCE_PRIO = 3200, SEUIL_PRIO = 2000;

  function horaires(messages) {
    var t = PREMIER, out = [];
    messages.forEach(function (m) {
      var c = m.priorite ? CADENCE_PRIO : CADENCE;
      out.push({debut: t, fin: t + c});
      t += c;
    });
    return out;
  }

  function regles(coef) {
    return "<div class=\"rules\"><h3>Règles</h3><ul>" +
      "<li>Vous êtes l'opérateur Sud-Est du PC Organisation, sur le canal Sud-Est. Votre indicatif radio : " +
      "<b>PC Org</b>.</li>" +
      "<li>Un message radio commence toujours par le destinataire, puis l'émetteur : « PC Org de Patrouille 2 » " +
      "veut dire que Patrouille 2 appelle le PC Org. Prenez uniquement les messages qui commencent par " +
      "« PC Org de… ».</li>" +
      "<li>Pour prendre un message : bouton <b>Je prends</b> ou barre d'espace.</li>" +
      "<li>Un message qui ne vous est pas adressé : ne faites rien. C'est le silence radio.</li>" +
      "<li>Un message <b>PRIORITÉ</b> pour vous doit être pris en moins de " + F.secondes(SEUIL_PRIO / 1000 * coef) +
      " secondes.</li></ul></div>";
  }

  // Deroule le canal dans `el`. opts : {t0, groupe, envoyer(action)|null, fin()}
  function canal(el, messages, coef, opts) {
    var g = opts.groupe, t0 = opts.t0, h = horaires(messages), pris = {};
    el.innerHTML = "<div class=\"radio\"><div class=\"radio-top\"><span>CANAL SUD-EST · PC ORG</span>" +
      "<div class=\"wave\" aria-hidden=\"true\">" + new Array(15).join("<i></i>") + "</div></div>" +
      "<div class=\"live\" aria-live=\"polite\">Écoute du canal…</div>" +
      "<button class=\"take\" type=\"button\">Je prends</button><ul class=\"log\"></ul></div>";
    var wave = el.querySelector(".wave"), live = el.querySelector(".live");
    var bouton = el.querySelector(".take"), journal = el.querySelector(".log");

    var ecoule = function () { return performance.now() - t0; };
    var noter = function (texte, cls) {
      var li = document.createElement("li");
      li.className = cls;
      li.textContent = texte;
      journal.insertBefore(li, journal.firstChild);
    };
    var courant = function () {
      var t = ecoule() / coef;
      for (var k = 0; k < h.length; k++) if (h[k].debut <= t && t < h[k].fin) return k;
      return -1;
    };
    var prendre = function () {
      var k = courant();
      if (k < 0 || pris[k]) return;
      pris[k] = true;
      var m = messages[k], t = ecoule();
      var dt = (t - h[k].debut * coef) / 1000;
      if (opts.envoyer) opts.envoyer({type: "prise", msg: m.id, t_ms: Math.round(t)});
      if (m.pour_moi) {
        noter("Pris (" + F.secondes(dt) + " s) · " + m.texte, "ok");
        F.son.bip(1400, 0.05);
      } else {
        noter("Pris à tort · " + m.texte, "ko");
        F.son.bip(220, 0.15, "sawtooth");
      }
    };
    bouton.onclick = prendre;
    g.on(document, "keydown", function (e) {
      if (e.code !== "Space" && e.key !== " ") return;
      var tag = (e.target && e.target.tagName) || "";
      if (tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA") return;
      e.preventDefault();
      if (!e.repeat) prendre();
    });

    var afficher = function (k) {
      var m = messages[k];
      live.textContent = m.texte;
      live.classList.toggle("prio", !!m.priorite);
      wave.classList.add("on");
      F.son.chirp();
      g.later(function () { wave.classList.remove("on"); }, 1500);
    };
    var clore = function (k) {
      if (pris[k]) return;
      var m = messages[k];
      if (m.pour_moi) noter("Manqué · " + m.texte, "ko");
      else noter("Laissé passer · " + m.texte, "na");
    };
    var depuis = function (msJeu) { return t0 + msJeu * coef - performance.now(); };
    h.forEach(function (x, k) {
      g.later(function () { afficher(k); }, depuis(x.debut));
      g.later(function () { clore(k); }, depuis(x.fin));
    });
    var derniere = h[h.length - 1].fin;
    g.later(function () {
      live.textContent = "Fin du trafic radio.";
      live.classList.remove("prio");
      bouton.disabled = true;
    }, depuis(derniere));
    g.later(opts.fin, depuis(derniere) + 600);
    return derniere * coef;
  }

  function entrainement(el, coef) {
    var contenu = null, groupe = null;
    el.innerHTML = "<div class=\"entrainement\"><h3>Entraînement</h3><p>Deux messages pour vous exercer. " +
      "Rien n'est enregistré : vous pouvez recommencer autant de fois que vous le souhaitez.</p>" +
      "<div class=\"row\"><button class=\"btn ghost\" type=\"button\">Lancer l'entraînement</button></div>" +
      "<div class=\"zone\"></div></div>";
    var bouton = el.querySelector("button"), zone = el.querySelector(".zone");
    var lancer = function () {
      if (groupe) groupe.stop();
      groupe = F.groupe();
      bouton.textContent = "Recommencer l'entraînement";
      canal(zone, contenu, coef, {t0: performance.now(), groupe: groupe, envoyer: null, fin: function () {
        var p = document.createElement("p");
        p.className = "enreg";
        p.textContent = "Entraînement terminé. Vous pouvez le recommencer ou commencer l'épreuve.";
        zone.appendChild(p);
      }});
    };
    bouton.onclick = function () {
      if (contenu) { lancer(); return; }
      bouton.disabled = true;
      F.api("/api/entrainement/radio").then(function (r) {
        bouton.disabled = false;
        if (r.status === 200 && r.data && r.data.ok && r.data.contenu && r.data.contenu.length) {
          contenu = r.data.contenu;
          lancer();
        } else if (r.status !== 401) {
          zone.innerHTML = "<p class=\"erreur\">Entraînement indisponible pour le moment.</p>";
        }
      });
    };
  }

  F.epreuves.radio = {
    intro: function (el, enonce, coef) {
      F.entete("Le canal est chargé. Prenez vos messages, laissez passer les autres.");
      el.innerHTML = regles(coef) + "<div></div>";
      entrainement(el.lastChild, coef);
    },
    jouer: function (el, enonce, coef, fini) {
      F.entete("Vous êtes PC Org, sur le canal Sud-Est.", true);
      var g = F.groupe();
      var total = canal(el, enonce.messages, coef, {
        t0: F.t0, groupe: g, fin: fini,
        envoyer: function (a) { F.action("radio", a); }
      });
      F.chrono(total, F.t0, g);
    }
  };
})();
