/* Rapports IA : briefing de situation (manager) et RETEX de fin d'edition (admin).
 *
 * IIFE autonome : injecte ses propres modales, ne depend que de toast.js
 * (showToast) s'il est charge. Ouvertures :
 *   - boutons portant data-air-open="briefing" ou data-air-open="retex"
 *   - window.AIReports.openBriefing() / window.AIReports.openRetex()
 * Tout le texte venu du serveur passe par textContent (aucun innerHTML).
 */
(function () {
  "use strict";

  var SOURCE_LABELS = {
    main_courante: "Main courante",
    alertes: "Centre d'alertes",
    presents: "Presents / affluence",
    trafic: "Trafic",
    meteo: "Meteo",
    field: "Tablettes Field",
    timetable: "Timetable (6 h)"
  };
  var SOURCE_ORDER = ["main_courante", "alertes", "presents", "trafic", "meteo", "field", "timetable"];
  var BRIEF_SECTIONS = [
    ["situation", "Situation"],
    ["points_chauds", "Points chauds"],
    ["a_surveiller_6h", "A surveiller (6 h)"],
    ["ressources", "Ressources"],
    ["consignes_releve", "Consignes de releve"]
  ];
  var BLOC_LABELS = {
    main_courante: "Main courante",
    comparaison_editions: "Comparaison editions",
    frequentation: "Frequentation (scans)",
    presents_live: "Presents (releves compteur)",
    alertes: "Centre d'alertes",
    field: "Tablettes Field / SOS",
    meteo: "Meteo",
    billetterie: "Billetterie"
  };
  var ERROR_LABELS = {
    cle_api_absente: "Cle API Anthropic absente sur le serveur : seul l'etat brut est disponible.",
    briefing_en_cours: "Un briefing IA est deja en cours pour cet evenement.",
    retex_en_cours: "Un RETEX est deja en cours pour cette edition.",
    budget_ia_depasse: "Budget IA mensuel depasse : generation bloquee.",
    ia_indisponible: "Le service IA n'a pas repondu. Reessayer plus tard.",
    role_admin_requis: "Reserve aux administrateurs.",
    role_manager_requis: "Reserve aux managers.",
    csrf: "Session expiree : recharger la page."
  };

  // ---------- helpers ----------

  function csrf() {
    var m = document.querySelector('meta[name="csrf-token"]');
    return m ? m.getAttribute("content") || "" : "";
  }

  function parse(r) {
    return r.json().catch(function () { return { ok: false, error: "reponse_invalide", status: r.status }; });
  }

  function apiGet(url) {
    return fetch(url, { credentials: "same-origin" }).then(parse)
      .catch(function () { return { ok: false, error: "reseau" }; });
  }

  function apiSend(method, url, body) {
    return fetch(url, {
      method: method,
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
      body: JSON.stringify(body || {})
    }).then(parse).catch(function () { return { ok: false, error: "reseau" }; });
  }

  function toast(type, msg) {
    if (typeof window.showToast === "function") { window.showToast(type, msg); return; }
    try { console.log("[ai-reports]", type, msg); } catch (e) { /* rien */ }
  }

  function errText(res) {
    var code = (res && res.error) || "erreur";
    return ERROR_LABELS[code] || ("Erreur : " + code + (res && res.detail ? " (" + res.detail + ")" : ""));
  }

  function isManager() { return window.__userIsManager === true || window.__userIsAdmin === true; }
  function isAdmin() { return window.__userIsAdmin === true; }

  function el(tag, attrs, children) {
    var n = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (k) {
        var v = attrs[k];
        if (v == null) return;
        if (k === "class") n.className = v;
        else if (k === "text") n.textContent = v;
        else if (k.indexOf("on") === 0 && typeof v === "function") n.addEventListener(k.slice(2), v);
        else n.setAttribute(k, v);
      });
    }
    (children || []).forEach(function (c) {
      if (c == null || c === false) return;
      n.appendChild(typeof c === "string" || typeof c === "number" ? document.createTextNode(String(c)) : c);
    });
    return n;
  }

  function clear(n) { while (n && n.firstChild) n.removeChild(n.firstChild); }

  function icon(name) { return el("span", { "class": "material-symbols-outlined", text: name }); }

  function fmt(v) {
    if (v === null || v === undefined || v === "") return "-";
    if (typeof v === "number") return v.toLocaleString("fr-FR");
    return String(v);
  }

  // Markdown leger (puces, paragraphes, **gras**) en noeuds DOM.
  function mdInline(line) {
    var out = [], rx = /\*\*([^*]+?)\*\*/g, last = 0, m;
    while ((m = rx.exec(line)) !== null) {
      if (m.index > last) out.push(document.createTextNode(line.slice(last, m.index)));
      out.push(el("strong", { text: m[1] }));
      last = rx.lastIndex;
    }
    if (last < line.length) out.push(document.createTextNode(line.slice(last)));
    return out;
  }

  function md(text) {
    var root = el("div", { "class": "air-md" });
    if (!text) { root.appendChild(el("p", { "class": "air-muted", text: "Non renseigne." })); return root; }
    var lines = String(text).replace(/\r\n/g, "\n").split("\n"), ul = null, p = null;
    lines.forEach(function (raw) {
      var s = raw.trim();
      if (s.indexOf("- ") === 0 || s.indexOf("* ") === 0) {
        p = null;
        if (!ul) { ul = el("ul"); root.appendChild(ul); }
        var li = el("li");
        mdInline(s.slice(2)).forEach(function (c) { li.appendChild(c); });
        ul.appendChild(li);
      } else if (!s) {
        ul = null; p = null;
      } else {
        ul = null;
        if (!p) { p = el("p"); root.appendChild(p); } else { p.appendChild(el("br")); }
        mdInline(s).forEach(function (c) { p.appendChild(c); });
      }
    });
    return root;
  }

  function kv(label, value, cls) {
    return el("div", { "class": "air-kv" + (cls ? " " + cls : "") }, [
      el("span", { "class": "air-kv-label", text: label }),
      el("span", { "class": "air-kv-value", text: fmt(value) })
    ]);
  }

  function chips(obj) {
    var box = el("div", { "class": "air-chips" });
    Object.keys(obj || {}).forEach(function (k) {
      box.appendChild(el("span", { "class": "air-chip", text: k + " : " + fmt(obj[k]) }));
    });
    if (!box.firstChild) box.appendChild(el("span", { "class": "air-muted", text: "aucun" }));
    return box;
  }

  function list(items, render, empty) {
    var ul = el("ul", { "class": "air-list" });
    (items || []).forEach(function (it) { ul.appendChild(el("li", null, [render(it)])); });
    if (!ul.firstChild) return el("p", { "class": "air-muted", text: empty || "aucun" });
    return ul;
  }

  function progressBar() {
    var fill = el("div", { "class": "air-progress-fill" });
    var label = el("div", { "class": "air-progress-label" });
    var box = el("div", { "class": "air-progress", hidden: "hidden" }, [el("div", { "class": "air-progress-track" }, [fill]), label]);
    return {
      node: box,
      set: function (pct, text) {
        box.removeAttribute("hidden");
        fill.style.width = Math.max(0, Math.min(100, pct || 0)) + "%";
        label.textContent = text || "";
      },
      hide: function () { box.setAttribute("hidden", "hidden"); }
    };
  }

  function poll(url, onTick, onDone, onError) {
    var stopped = false;
    function tick() {
      if (stopped) return;
      apiGet(url).then(function (res) {
        if (stopped) return;
        if (!res || !res.ok) { onError(res); return; }
        onTick(res);
        if (res.status === "done") onDone(res);
        else if (res.status === "error") onError(res);
        else setTimeout(tick, 1500);
      });
    }
    tick();
    return function () { stopped = true; };
  }

  // ---------- modale generique ----------

  // host fourni : mode INTEGRE (onglet de la modale Assistant IA, cf.
  // ai_assistant.js) -- ni overlay, ni en-tete, ni fermeture propres.
  function Modal(id, title, iconName, host) {
    var self = this;
    this.subtitle = el("div", { "class": "air-modal-subtitle" });
    this.toolbar = el("div", { "class": "air-toolbar" });
    this.body = el("div", { "class": "air-body" });
    if (host) {
      this.embedded = true;
      this.modal = el("div", { "class": "air-embedded" }, [this.toolbar, this.body]);
      host.appendChild(this.modal);
      return;
    }
    var closeBtn = el("button", { "class": "air-close", type: "button", title: "Fermer", "aria-label": "Fermer" }, [icon("close")]);
    this.modal = el("div", { "class": "air-modal", role: "dialog", "aria-modal": "true", "aria-label": title }, [
      el("div", { "class": "air-header" }, [
        el("span", { "class": "material-symbols-outlined air-header-icon", text: iconName }),
        el("div", { "class": "air-titles" }, [el("h2", { "class": "air-title", text: title }), this.subtitle]),
        closeBtn
      ]),
      this.toolbar,
      this.body
    ]);
    this.overlay = el("div", { "class": "air-overlay", id: id }, [this.modal]);
    closeBtn.addEventListener("click", function () { self.close(); });
    this.overlay.addEventListener("mousedown", function (e) { if (e.target === self.overlay) self.close(); });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && self.overlay.classList.contains("is-open")) self.close();
    });
    document.body.appendChild(this.overlay);
  }
  Modal.prototype.open = function () { if (!this.embedded) this.overlay.classList.add("is-open"); };
  Modal.prototype.close = function () {
    if (!this.embedded) this.overlay.classList.remove("is-open");
    if (this.onClose) this.onClose();
  };

  function button(label, iconName, onClick, cls) {
    return el("button", { "class": "air-btn" + (cls ? " " + cls : ""), type: "button", onclick: onClick },
      [iconName ? icon(iconName) : null, el("span", { text: label })]);
  }

  // ---------- briefing : rendu du contexte brut ----------

  function srcData(ctx, name) {
    var s = ((ctx && ctx.sources) || {})[name];
    return s && s.statut === "ok" ? s.data : null;
  }

  var RENDER = {
    main_courante: function (d) {
      var o = d.ouvertes || {}, r = d.dernieres_3h || {};
      return [
        el("div", { "class": "air-kvs" }, [kv("Fiches ouvertes", o.total, "air-strong"),
          kv("Ouvertes > 12 h", o.ouvertes_depuis_plus_de_12h), kv("Fiches (3 h)", r.total),
          kv("Closes (3 h)", r.closes), kv("Total edition", d.total_edition)]),
        el("h5", { text: "Ouvertes par categorie" }), chips(o.par_categorie),
        el("h5", { text: "Ouvertes par urgence" }), chips(o.par_urgence),
        el("h5", { text: "Majeures ouvertes les plus anciennes" }),
        list(o.majeures_les_plus_anciennes, ficheLine, "aucune fiche majeure ouverte"),
        el("h5", { text: "Majeures des 3 dernieres heures" }),
        list(r.majeures, ficheLine, "aucune")
      ];
    },
    alertes: function (d) {
      var a = d.actives || {}, h = d.historique_3h || {};
      return [
        el("div", { "class": "air-kvs" }, [kv("Actives", a.total, "air-strong"),
          kv("Non prises en charge", a.non_prises_en_charge), kv("Historique 3 h", h.total)]),
        list(a.liste, function (x) {
          return el("span", null, [el("b", { text: (x.declenchee || "") + " " + (x.type || "") + " " }),
            document.createTextNode(x.titre || ""),
            x.prise_en_charge ? el("em", { text: " (prise par " + (x.prise_en_charge.par || "?") + ")" }) : null]);
        }, "aucune alerte active"),
        el("h5", { text: "3 dernieres heures par type" }), chips(h.par_type)
      ];
    },
    presents: function (d) {
      var b = d.billetterie_aujourdhui || {}, y = d.hier || {};
      var out = [el("div", { "class": "air-kvs" }, [
        kv("Presents actuels", d.presents_actuels, "air-strong"),
        kv("Dernier releve", d.dernier_releve),
        kv("Pic du jour", d.pic_du_jour != null ? fmt(d.pic_du_jour) + (d.pic_du_jour_heure ? " a " + d.pic_du_jour_heure : "") : null),
        kv("Pic N-1 jour equivalent", d.pic_n1_jour_equivalent),
        kv("Pic projete", b.pic_projete != null ? fmt(b.pic_projete) + (b.heure_pic_attendue ? " vers " + b.heure_pic_attendue : "") : null),
        kv("Billets du jour", b.billets_vendus),
        kv("Pic constate hier", y.pic_constate != null ? fmt(y.pic_constate) + (y.heure ? " a " + y.heure : "") : null)
      ])];
      if (d.note) out.push(el("p", { "class": "air-note", text: d.note }));
      return out;
    },
    trafic: function (d) {
      var a = d.alertes_waze_en_zone || {};
      var out = [el("div", { "class": "air-kvs" }, [
        kv("Verdict", d.verdict || "non affirme", "air-strong air-verdict-" + String(d.verdict || "na").toLowerCase()),
        kv("Accidents", a.accidents), kv("Bouchons", a.bouchons), kv("Dangers", a.dangers),
        kv("Releve (min)", d.age_releve_routes_min)
      ])];
      if (d.note) out.push(el("p", { "class": "air-note", text: d.note }));
      out.push(list(d.pires_axes, function (x) {
        return el("span", null, [el("b", { text: x.axe + " " }), document.createTextNode(
          "(" + x.sens + ") " + Math.round((x.temps_s || 0) / 60) + " min, retard +" + fmt(x.retard_s) + " s, severite " + x.severite_0_4 + "/4")]);
      }, "aucun axe"));
      return out;
    },
    meteo: function (d) {
      var a = d.actuel || {}, p = d.prochaine_pluie || {};
      var out = [el("div", { "class": "air-kvs" }, [
        kv("Temperature", a.temperature_c != null ? a.temperature_c + " C" : null),
        kv("Rafales", a.vent_rafale_kmh != null ? a.vent_rafale_kmh + " km/h" : null),
        kv("Pluie", p.attendue ? "dans " + fmt(p.dans_min) + " min" : "non attendue"),
        kv("Vigilance", d.vigilance_couleur_jour)
      ])];
      if (d.verdict && d.verdict.titre) out.push(el("p", { "class": "air-note", text: d.verdict.titre + (d.verdict.detail ? " - " + d.verdict.detail : "") }));
      out.push(list((d.consignes || []).map(function (c) { return c.heure + " " + c.niveau + " : " + c.texte; })
        .concat((d.contraintes_non_normales || []).map(function (c) { return c.libelle + " (" + c.niveau + ") : " + (c.consigne || ""); })),
        function (t) { return document.createTextNode(t); }, "aucune consigne"));
      return out;
    },
    field: function (d) {
      var s = d.sos || {};
      return [
        el("div", { "class": "air-kvs" }, [kv("Tablettes", d.tablettes_enrolees), kv("Disponibles", d.disponibles_en_ligne, "air-strong"),
          kv("Engagees", (d.engagees || []).length), kv("Hors ligne", (d.hors_ligne || []).length),
          kv("SOS actifs", (s.alertes_sos_actives || []).length), kv("SOS (3 h)", s.sos_dernieres_3h)]),
        list(d.engagees, function (x) {
          return el("span", null, [el("b", { text: x.nom + " " }), document.createTextNode(
            x.statut + (x.depuis ? " depuis " + x.depuis : "") + (x.fiche ? " - " + (x.fiche.categorie || "") + " " + (x.fiche.texte || "") : ""))]);
        }, "aucune tablette engagee")
      ];
    },
    timetable: function (d) {
      return [list(d.vignettes, function (x) {
        return el("span", null, [el("b", { text: (x.heure || "") + " " }), document.createTextNode((x.activite || "") + (x.lieu ? " - " + x.lieu : ""))]);
      }, "aucune vignette dans les 6 prochaines heures")];
    }
  };

  function ficheLine(f) {
    return el("span", null, [
      el("b", { text: (f.heure || "") + " " }),
      el("span", { "class": "air-tag", text: (f.urgence || "-") }),
      document.createTextNode(" " + (f.categorie || "") + (f.zone ? " [" + f.zone + "]" : "") + " " + (f.texte || "") + (f.ouverte ? " (ouverte)" : ""))
    ]);
  }

  function renderContext(target, ctx) {
    clear(target);
    if (!ctx) return;
    target.appendChild(el("div", { "class": "air-ctx-head" }, [
      el("span", { text: "Etat au " + (ctx.genere_a || "?").replace("T", " ") + " - " + (ctx.event || "?") + " " + (ctx.year || "") }),
      el("span", { "class": "air-muted", text: " (" + fmt(ctx.duree_ms) + " ms)" })
    ]));
    var grid = el("div", { "class": "air-grid" });
    SOURCE_ORDER.forEach(function (name) {
      var s = (ctx.sources || {})[name];
      if (!s) return;
      var card = el("section", { "class": "air-card" + (s.statut !== "ok" ? " air-card-ko" : "") }, [
        el("h4", { text: SOURCE_LABELS[name] || name })
      ]);
      if (s.statut !== "ok") {
        card.appendChild(el("p", { "class": "air-ko", text: "Donnee indisponible : " + (s.erreur || "erreur inconnue") }));
      } else {
        try {
          (RENDER[name] ? RENDER[name](s.data || {}) : []).forEach(function (n) { card.appendChild(n); });
        } catch (e) {
          card.appendChild(el("p", { "class": "air-muted", text: "Affichage impossible, voir le detail." }));
        }
        var det = el("details", { "class": "air-details" }, [el("summary", { text: "Detail brut" }),
          el("pre", { text: JSON.stringify(s.data, null, 1) })]);
        card.appendChild(det);
      }
      grid.appendChild(card);
    });
    target.appendChild(grid);
  }

  function renderSections(target, sections, raw) {
    clear(target);
    if (!sections) {
      target.appendChild(el("p", { "class": "air-note", text: "Reponse IA non structuree :" }));
      target.appendChild(el("pre", { "class": "air-pre", text: raw || "" }));
      return;
    }
    BRIEF_SECTIONS.forEach(function (s) {
      target.appendChild(el("section", { "class": "air-section air-section-" + s[0] }, [
        el("h4", { text: s[1] }), md(sections[s[0]])
      ]));
    });
  }

  // ---------- briefing : modale ----------

  var brief = null;

  function buildBriefing(host) {
    var m = new Modal("air-briefing-modal", "Briefing de situation", "assignment", host);
    var st = { stopPoll: null };
    var useSel = el("input", { type: "checkbox", id: "air-brief-usesel" });
    var selLabel = el("label", { "class": "air-check", "for": "air-brief-usesel" }, [useSel, el("span", { text: "Evenement selectionne" })]);
    var prog = progressBar();
    var ai = el("div", { "class": "air-ai" });
    var raw = el("div", { "class": "air-raw" });
    var panel = el("div", { "class": "air-panel", hidden: "hidden" });

    function payload(llm) {
      var b = { llm: llm };
      if (useSel.checked && window.selectedEvent && window.selectedYear) {
        b.event = window.selectedEvent; b.year = String(window.selectedYear);
      }
      return b;
    }

    function showMain() { panel.setAttribute("hidden", "hidden"); ai.removeAttribute("hidden"); raw.removeAttribute("hidden"); }

    function runRaw() {
      showMain();
      prog.set(20, "Collecte des sources...");
      return apiSend("POST", "/api/ai/briefing", payload(false)).then(function (res) {
        prog.hide();
        if (!res.ok) { toast("error", errText(res)); return; }
        renderContext(raw, res.context);
        m.subtitle.textContent = (res.context.event || "?") + " " + (res.context.year || "") + " - etat brut, sans IA";
      });
    }

    function runAI() {
      showMain();
      if (st.stopPoll) st.stopPoll();
      clear(ai);
      ai.appendChild(el("p", { "class": "air-muted", text: "Redaction en cours..." }));
      runRaw().then(function () {
        apiSend("POST", "/api/ai/briefing", payload(true)).then(function (res) {
          if (!res.ok) {
            clear(ai);
            ai.appendChild(el("p", { "class": "air-note", text: errText(res) }));
            if (res.error === "briefing_en_cours" && res.job_id) follow(res.job_id); else toast("warning", errText(res));
            return;
          }
          follow(res.job_id);
        });
      });
    }

    function follow(jobId) {
      var ctxShown = false;
      st.stopPoll = poll("/api/ai/briefing/status?job=" + encodeURIComponent(jobId), function (job) {
        prog.set(job.progress, job.step);
        if (job.context && !ctxShown) { ctxShown = true; renderContext(raw, job.context); }
      }, function (job) {
        prog.hide();
        renderSections(ai, job.result && job.result.sections, job.result && job.result.raw_text);
        toast("success", "Briefing IA pret.");
      }, function (job) {
        prog.hide();
        clear(ai);
        ai.appendChild(el("p", { "class": "air-note", text: errText(job) }));
        toast("error", errText(job));
      });
    }

    function showHistory() {
      panel.removeAttribute("hidden"); ai.setAttribute("hidden", "hidden"); raw.setAttribute("hidden", "hidden");
      clear(panel);
      panel.appendChild(el("h4", { text: "Briefings IA enregistres" }));
      apiGet("/api/ai/briefing/list?limit=40").then(function (res) {
        if (!res.ok) { panel.appendChild(el("p", { "class": "air-note", text: errText(res) })); return; }
        panel.appendChild(list(res.items, function (it) {
          return el("button", { "class": "air-link", type: "button", onclick: function () { openBriefingDoc(it.id); } }, [
            document.createTextNode((it.ts || "").replace("T", " ") + " - " + (it.event || "?") + " " + (it.year || "") + " - " + (it.by || "") + (it.trigger === "releve" ? " (releve auto)" : ""))
          ]);
        }, "aucun briefing enregistre"));
      });
    }

    function openBriefingDoc(id) {
      apiGet("/api/ai/briefing/" + encodeURIComponent(id)).then(function (res) {
        if (!res.ok) { toast("error", errText(res)); return; }
        showMain();
        var b = res.briefing;
        m.subtitle.textContent = "Briefing du " + (b.ts || "").replace("T", " ") + " - " + (b.event || "") + " " + (b.year || "");
        renderContext(raw, b.context);
        renderSections(ai, b.sections, b.raw_text);
      });
    }

    function showSettings() {
      panel.removeAttribute("hidden"); ai.setAttribute("hidden", "hidden"); raw.setAttribute("hidden", "hidden");
      clear(panel);
      panel.appendChild(el("h4", { text: "Briefing automatique a chaque releve" }));
      panel.appendChild(el("p", { "class": "air-muted", text: "La tache planifiee (scripts/briefing_releve.py, toutes les 15 min) envoie le briefing par mail aux heures indiquees, si active." }));
      apiGet("/api/ai/briefing/settings").then(function (res) {
        if (!res.ok) { panel.appendChild(el("p", { "class": "air-note", text: errText(res) })); return; }
        var s = res.settings || {};
        var enabled = el("input", { type: "checkbox", id: "air-rel-enabled" });
        enabled.checked = !!s.enabled;
        var times = el("input", { type: "text", "class": "air-input", value: (s.times || []).join(", "), placeholder: "06:45, 14:45, 22:45" });
        var filter = el("input", { type: "search", "class": "air-input", placeholder: "Filtrer les utilisateurs..." });
        var chosen = {};
        (s.recipients || []).forEach(function (id) { chosen[id] = true; });
        var usersBox = el("div", { "class": "air-users" });
        function drawUsers() {
          clear(usersBox);
          var q = filter.value.trim().toLowerCase();
          (res.users || []).forEach(function (u) {
            if (q && (u.name + " " + u.email).toLowerCase().indexOf(q) === -1 && !chosen[u.id]) return;
            var cb = el("input", { type: "checkbox" });
            cb.checked = !!chosen[u.id];
            cb.addEventListener("change", function () { if (cb.checked) chosen[u.id] = true; else delete chosen[u.id]; });
            usersBox.appendChild(el("label", { "class": "air-user" }, [cb, el("span", { text: u.name }), el("span", { "class": "air-muted", text: " " + u.email })]));
          });
        }
        filter.addEventListener("input", drawUsers);
        drawUsers();
        if (!res.api_key) panel.appendChild(el("p", { "class": "air-note", text: "Cle API absente : le mail ne contiendra que les chiffres bruts." }));
        panel.appendChild(el("label", { "class": "air-check", "for": "air-rel-enabled" }, [enabled, el("span", { text: "Activer l'envoi automatique" })]));
        panel.appendChild(el("div", { "class": "air-field" }, [el("span", { text: "Heures de releve (HH:MM, separees par des virgules)" }), times]));
        panel.appendChild(el("div", { "class": "air-field" }, [el("span", { text: "Destinataires" }), filter, usersBox]));
        if (s.last_slots && s.last_slots.length) {
          panel.appendChild(el("p", { "class": "air-muted", text: "Derniers envois : " + s.last_slots.slice(-5).join(" | ") }));
        }
        panel.appendChild(button("Enregistrer", "save", function () {
          var t = times.value.split(/[,;\s]+/).filter(function (x) { return x; });
          apiSend("PUT", "/api/ai/briefing/settings", { enabled: enabled.checked, times: t, recipients: Object.keys(chosen) })
            .then(function (r) {
              if (!r.ok) { toast("error", errText(r)); return; }
              toast("success", "Reglages du briefing de releve enregistres.");
              times.value = (r.settings.times || []).join(", ");
            });
        }, "air-btn-primary"));
      });
    }

    m.toolbar.appendChild(button("Etat brut (gratuit)", "bolt", runRaw));
    m.toolbar.appendChild(button("Briefing IA", "auto_awesome", runAI, "air-btn-primary"));
    m.toolbar.appendChild(button("Historique", "history", showHistory));
    if (isAdmin()) m.toolbar.appendChild(button("Releve auto", "schedule_send", showSettings));
    m.toolbar.appendChild(selLabel);
    m.body.appendChild(prog.node);
    m.body.appendChild(el("div", { "class": "air-split" }, [
      el("div", { "class": "air-col" }, [el("h3", { "class": "air-col-title", text: "Redaction IA" }), ai]),
      el("div", { "class": "air-col air-col-wide" }, [el("h3", { "class": "air-col-title", text: "Donnees brutes" }), raw])
    ]));
    m.body.appendChild(panel);
    ai.appendChild(el("p", { "class": "air-muted", text: "Cliquer sur << Briefing IA >> pour une redaction a partir des donnees ci-contre." }));
    m.onClose = function () { if (st.stopPoll) st.stopPoll(); };
    m.runRaw = runRaw;
    m.useSel = useSel;
    return m;
  }

  // ---------- mode integre (onglets de la modale Assistant IA) ----------
  // Un seul point d'entree IA dans la sidebar : Resume, Briefing et RETEX
  // vivent dans la meme modale (ai_assistant.js), qui monte ces vues a la
  // premiere ouverture de leur onglet.
  var embedded = {};

  function mountBriefing(host) {
    if (!isManager()) { toast("warning", ERROR_LABELS.role_manager_requis); return null; }
    if (!embedded.briefing) embedded.briefing = buildBriefing(host);
    embedded.briefing.useSel.checked = false;
    embedded.briefing.runRaw();
    return embedded.briefing;
  }

  function mountRetex(host) {
    if (!isAdmin()) { toast("warning", ERROR_LABELS.role_admin_requis); return null; }
    var first = !embedded.retex;
    if (first) embedded.retex = buildRetex(host);
    if (first) embedded.retex.loadEditions();
    return embedded.retex;
  }

  // Fermeture de la modale Assistant : arrete les suivis de jobs en cours.
  function hideEmbedded() {
    Object.keys(embedded).forEach(function (k) {
      var m = embedded[k];
      if (m && m.onClose) m.onClose();
    });
  }

  function openBriefing() {
    if (window.AIAssistant && window.AIAssistant.open) { window.AIAssistant.open("briefing"); return; }
    if (!isManager()) { toast("warning", ERROR_LABELS.role_manager_requis); return; }
    if (!brief) brief = buildBriefing();
    brief.useSel.checked = false;
    brief.subtitle.textContent = "Etat du site maintenant, pour la releve";
    brief.open();
    brief.runRaw();
  }

  // ---------- RETEX ----------

  var retex = null;

  function buildRetex(host) {
    var m = new Modal("air-retex-modal", "RETEX de fin d'edition", "summarize", host);
    var st = { stopPoll: null, editions: [] };
    var select = el("select", { "class": "air-input air-select" });
    var prog = progressBar();
    var out = el("div", { "class": "air-retex-out" });
    var hist = el("div", { "class": "air-retex-hist" });

    function current() {
      var v = select.value.split("|");
      return { event: v[0], year: v[1] };
    }

    function loadEditions() {
      return apiGet("/api/ai/retex/editions").then(function (res) {
        if (!res.ok) { toast("error", errText(res)); return; }
        clear(select);
        st.editions = res.items || [];
        st.editions.forEach(function (e) {
          select.appendChild(el("option", { value: e.event + "|" + e.year, text: e.event + " " + e.year }));
        });
        var want = (window.selectedEvent || "") + "|" + (window.selectedYear || "");
        for (var i = 0; i < select.options.length; i++) {
          if (select.options[i].value === want) { select.selectedIndex = i; break; }
        }
        loadHistory();
      });
    }

    function loadHistory() {
      var c = current();
      clear(hist);
      hist.appendChild(el("h4", { text: "Versions enregistrees - " + c.event + " " + c.year }));
      apiGet("/api/ai/retex/list?event=" + encodeURIComponent(c.event) + "&year=" + encodeURIComponent(c.year)).then(function (res) {
        if (!res.ok) { hist.appendChild(el("p", { "class": "air-note", text: errText(res) })); return; }
        hist.appendChild(list(res.items, function (it) {
          return el("span", null, [
            el("b", { text: "v" + it.version + " " }),
            document.createTextNode((it.created_at || "").replace("T", " ") + " - " + (it.created_by || "") + " - " + (it.model || "") + (it.truncated ? " (tronque)" : "") + " "),
            el("a", { href: "/api/ai/retex/" + encodeURIComponent(it.id) + "/html", target: "_blank", rel: "noopener", "class": "air-link", text: "Ouvrir / imprimer" })
          ]);
        }, "aucun RETEX pour cette edition"));
      });
    }

    function dryRun() {
      var c = current();
      clear(out);
      prog.set(30, "Assemblage des donnees (sans appel IA)...");
      apiSend("POST", "/api/ai/retex", { event: c.event, year: c.year, dry_run: true }).then(function (res) {
        prog.hide();
        if (!res.ok) { toast("error", errText(res)); return; }
        out.appendChild(el("h4", { text: "Dry run - " + c.event + " " + c.year }));
        out.appendChild(el("div", { "class": "air-kvs" }, [
          kv("Assemblage", fmt(res.build_ms) + " ms"), kv("Prompt", fmt(res.prompt_chars) + " car."),
          kv("Tokens estimes (entree)", res.tokens_estimes, "air-strong"), kv("Sortie max", res.max_tokens_sortie)
        ]));
        var tbl = el("table", { "class": "air-table" }, [el("thead", null, [el("tr", null, [
          el("th", { text: "Bloc" }), el("th", { text: "Statut" }), el("th", { text: "ms" }), el("th", { text: "Taille" }), el("th", { text: "Raison" })])])]);
        var tb = el("tbody");
        Object.keys(res.blocs || {}).forEach(function (k) {
          var b = res.blocs[k];
          tb.appendChild(el("tr", { "class": b.statut === "ok" ? "" : "air-row-ko" }, [
            el("td", { text: BLOC_LABELS[k] || k }), el("td", { text: b.statut }), el("td", { text: fmt(b.ms) }),
            el("td", { text: fmt(b.chars) }), el("td", { text: b.erreur || "" })]));
        });
        tbl.appendChild(tb);
        out.appendChild(tbl);
        out.appendChild(el("h5", { text: "Reserves transmises au modele" }));
        out.appendChild(list(res.avertissements, function (t) { return document.createTextNode(t); }, "aucune"));
        out.appendChild(el("details", { "class": "air-details" }, [el("summary", { text: "Prompt complet" }),
          el("pre", { text: (res.system || "") + "\n\n----- USER -----\n\n" + (res.user || "") })]));
      });
    }

    function generate() {
      var c = current();
      var go = function () {
        clear(out);
        prog.set(3, "Demarrage...");
        apiSend("POST", "/api/ai/retex", { event: c.event, year: c.year }).then(function (res) {
          if (!res.ok) {
            if (res.error === "retex_en_cours" && res.job_id) { follow(res.job_id); return; }
            prog.hide(); toast("error", errText(res)); return;
          }
          follow(res.job_id);
        });
      };
      // showConfirmToast rend une Promise<boolean> (false si pas de conteneur
      // de toasts sur la page : on ne bloque pas l'admin pour autant).
      if (typeof window.showConfirmToast === "function" && document.getElementById("toast-container")) {
        window.showConfirmToast("Generer le RETEX " + c.event + " " + c.year + " ? Appel IA facture, 1 a 3 min.",
          { okLabel: "Generer", cancelLabel: "Annuler" }).then(function (ok) { if (ok) go(); });
      } else { go(); }
    }

    function follow(jobId) {
      if (st.stopPoll) st.stopPoll();
      st.stopPoll = poll("/api/ai/retex/status?job=" + encodeURIComponent(jobId), function (job) {
        prog.set(job.progress, job.step);
      }, function (job) {
        prog.hide();
        var r = job.result || {};
        clear(out);
        out.appendChild(el("p", { "class": "air-ok", text: "RETEX v" + r.version + " genere" + (r.truncated ? " (reponse tronquee)" : "") + "." }));
        out.appendChild(el("a", { href: "/api/ai/retex/" + encodeURIComponent(r.id) + "/html", target: "_blank", rel: "noopener", "class": "air-btn air-btn-primary", text: "Ouvrir le rapport imprimable" }));
        toast("success", "RETEX genere.");
        loadHistory();
      }, function (job) {
        prog.hide();
        toast("error", errText(job));
        out.appendChild(el("p", { "class": "air-note", text: errText(job) }));
      });
    }

    select.addEventListener("change", function () { clear(out); loadHistory(); });
    m.toolbar.appendChild(select);
    m.toolbar.appendChild(button("Dry run (gratuit)", "science", dryRun));
    m.toolbar.appendChild(button("Generer le RETEX", "auto_awesome", generate, "air-btn-primary"));
    m.body.appendChild(prog.node);
    m.body.appendChild(el("div", { "class": "air-split" }, [
      el("div", { "class": "air-col air-col-wide" }, [out]),
      el("div", { "class": "air-col" }, [hist])
    ]));
    m.onClose = function () { if (st.stopPoll) st.stopPoll(); };
    m.loadEditions = loadEditions;
    return m;
  }

  function openRetex() {
    if (window.AIAssistant && window.AIAssistant.open) { window.AIAssistant.open("retex"); return; }
    if (!isAdmin()) { toast("warning", ERROR_LABELS.role_admin_requis); return; }
    if (!retex) retex = buildRetex();
    retex.subtitle.textContent = "Rapport complet d'une edition, imprimable";
    retex.open();
    retex.loadEditions();
  }

  // ---------- liaisons ----------

  function bind() {
    document.querySelectorAll("[data-air-open]").forEach(function (b) {
      if (b.getAttribute("data-air-bound")) return;
      b.setAttribute("data-air-bound", "1");
      b.setAttribute("role", "button");
      b.setAttribute("tabindex", "0");
      var which = b.getAttribute("data-air-open");
      var fn = which === "retex" ? openRetex : openBriefing;
      b.addEventListener("click", function (e) { e.preventDefault(); fn(); });
      b.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fn(); } });
    });
  }

  window.AIReports = {
    openBriefing: openBriefing, openRetex: openRetex,
    mountBriefing: mountBriefing, mountRetex: mountRetex, hideEmbedded: hideEmbedded,
    isAdmin: isAdmin
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", bind);
  else bind();
})();
