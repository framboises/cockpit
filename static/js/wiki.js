/* Wiki des procédures PC Orga — consultation + fonctions de rendu partagées.
   Expose window.Wiki { esc, flowHtml, cardHtml } pour réutilisation (admin).
   L'initialisation de la consultation ne se déclenche que si #wiki-grid existe. */
(function () {
  function esc(s) {
    return (s == null ? "" : String(s)).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }

  function flowHtml(flow) {
    var h = '<div class="wk-flow">';
    (flow || []).forEach(function (nd) {
      var k = nd.k || "";
      if (k === "ask") {
        h += '<div class="wk-node wk-ask"><span class="wk-tg">Décision</span>' + esc(nd.t) + "</div>";
        h += '<div class="wk-branch">';
        h += '<div class="wk-bchip wk-yes"><span class="wk-lab">OUI</span>' + esc(nd.y) + "</div>";
        if (nd.n && nd.n !== "—" && nd.n !== "poursuivre")
          h += '<div class="wk-bchip wk-no"><span class="wk-lab">NON</span>' + esc(nd.n) + "</div>";
        else
          h += '<div class="wk-bchip wk-no wk-muted"><span class="wk-lab">NON</span>poursuivre</div>';
        h += "</div>";
      } else if (k === "watch") {
        h += '<div class="wk-node wk-watch"><span class="wk-ic">◉</span>' + esc(nd.t) + "</div>";
      } else if (k === "engage") {
        h += '<div class="wk-node wk-engage"><span class="wk-tg">Engager</span>' + esc(nd.t) + "</div>";
      } else {
        h += '<div class="wk-node wk-' + esc(k) + '">' + esc(nd.t) + "</div>";
      }
    });
    return h + "</div>";
  }

  function sec(title, inner) {
    return '<div class="wk-sec"><h4>' + esc(title) + "</h4>" + inner + "</div>";
  }

  function cardHtml(p, catMap) {
    var cat = (catMap && catMap[p.dom]) || { label: p.dom, color: "#64748b" };
    var steps = (p.conduite || []).map(function (s) { return "<li>" + esc(s) + "</li>"; }).join("");
    var subs = (p.souscas || []).map(function (s) { return "<span>" + esc(s) + "</span>"; }).join("");
    var ques = (p.questions || []).map(function (s) { return "<li>" + esc(s) + "</li>"; }).join("");
    var tips = (p.details || []).map(function (s) { return "<li>" + esc(s) + "</li>"; }).join("");
    var badge = p.status === "draft" ? '<span class="wk-badge">brouillon</span>' : "";
    var txt = ((p.code || "") + " " + (p.titre || "") + " " + (p.situation || "") + " " +
      (p.souscas || []).join(" ") + " " + (p.acteurs || "") + " " +
      (p.questions || []).join(" ") + " " + (p.details || []).join(" ")).toLowerCase();

    return '<div class="wk-card" data-dom="' + esc(p.dom) + '" data-txt="' + esc(txt) + '">' +
      '<div class="wk-chead" tabindex="0" role="button" aria-expanded="false" ' +
      'onclick="WikiToggle(this)" onkeydown="if(event.key===\'Enter\'||event.key===\' \'){event.preventDefault();WikiToggle(this)}">' +
      '<div class="wk-stripe" style="background:' + cat.color + '"></div>' +
      '<div class="wk-code" style="color:' + cat.color + '">' + esc(p.code) + "</div>" +
      '<div class="wk-title"><span class="wk-dom">' + esc(cat.label) + "</span>" + esc(p.titre) + "</div>" +
      badge +
      '<div class="wk-caret">▶</div>' +
      "</div>" +
      '<div class="wk-cbody">' +
      sec("Situation", "<p>" + esc(p.situation || "") + "</p>") +
      (ques ? '<div class="wk-qbox"><h4>Les bonnes questions à poser</h4><ul class="wk-qlist">' + ques + "</ul></div>" : "") +
      ((p.flow && p.flow.length) ? '<div class="wk-flowsec"><h4>Le cheminement</h4>' + flowHtml(p.flow) + "</div>" : "") +
      (subs ? sec("Sous-cas", '<div class="wk-subcas">' + subs + "</div>") : "") +
      '<div class="wk-cols"><div>' +
      sec("Qui engager", "<p>" + esc(p.acteurs || "") + "</p>") +
      (steps ? '<div class="wk-sec"><h4>Conduite à tenir, en détail</h4><ol class="wk-steps">' + steps + "</ol></div>" : "") +
      "</div><div>" +
      sec("À consigner", "<p>" + esc(p.consigner || "") + "</p>") +
      '<div class="wk-sec wk-warn"><h4>Pièges</h4><p>' + esc(p.pieges || "") + "</p></div>" +
      "</div></div>" +
      (tips ? '<div class="wk-sec wk-tips"><h4>Réflexes terrain — ce que font les opérateurs</h4><ul class="wk-tiplist">' + tips + "</ul></div>" : "") +
      "</div></div>";
  }

  window.Wiki = { esc: esc, flowHtml: flowHtml, cardHtml: cardHtml };
  window.WikiToggle = function (el) {
    var open = el.parentNode.classList.toggle("wk-open");
    el.setAttribute("aria-expanded", open);
  };

  /* ---------- Consultation (seulement si le conteneur existe) ---------- */
  var grid = document.getElementById("wiki-grid");
  if (!grid) return;

  var searchEl = document.getElementById("wiki-search");
  var filtersEl = document.getElementById("wiki-filters");
  var countEl = document.getElementById("wiki-count");
  var noresEl = document.getElementById("wiki-nores");
  var activeDom = "all";

  function apply() {
    var term = (searchEl.value || "").trim().toLowerCase();
    var n = 0;
    grid.querySelectorAll(".wk-card").forEach(function (c) {
      var okd = activeDom === "all" || c.dataset.dom === activeDom;
      var okt = !term || c.dataset.txt.indexOf(term) !== -1;
      var show = okd && okt;
      c.classList.toggle("wk-hidden", !show);
      if (show) n++;
    });
    countEl.textContent = n + " procédure" + (n > 1 ? "s" : "") + (activeDom !== "all" || term ? " affichée" + (n > 1 ? "s" : "") : "");
    noresEl.style.display = n ? "none" : "block";
  }

  function wireFilters() {
    filtersEl.querySelectorAll(".wk-chip").forEach(function (ch) {
      var act = function () {
        filtersEl.querySelectorAll(".wk-chip").forEach(function (x) { x.classList.remove("wk-on"); x.style.background = ""; });
        ch.classList.add("wk-on");
        activeDom = ch.dataset.dom;
        if (activeDom !== "all" && ch.dataset.color) ch.style.background = ch.dataset.color;
        apply();
      };
      ch.addEventListener("click", act);
      ch.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); act(); } });
    });
  }

  function jget(url) { return fetch(url, { credentials: "same-origin" }).then(function (r) { return r.json(); }); }

  Promise.all([jget("/api/wiki/categories"), jget("/api/wiki/procedures")])
    .then(function (res) {
      var cats = res[0] || [], procs = res[1] || [];
      var catMap = {};
      cats.forEach(function (c) { catMap[c.key] = { label: c.label, color: c.color }; });
      filtersEl.innerHTML =
        '<div class="wk-chip wk-on" data-dom="all" tabindex="0" role="button">Toutes</div>' +
        cats.map(function (c) {
          return '<div class="wk-chip" data-dom="' + c.key + '" data-color="' + c.color + '" tabindex="0" role="button">' +
            '<span class="wk-dot" style="background:' + c.color + '"></span>' + esc(c.label) + "</div>";
        }).join("");
      grid.innerHTML = procs.map(function (p) { return cardHtml(p, catMap); }).join("");
      searchEl.addEventListener("input", apply);
      wireFilters();
      apply();
    })
    .catch(function () {
      grid.innerHTML = '<div class="wk-empty">Erreur de chargement du wiki. Réessayez.</div>';
    });
})();
