/**
 * Import de scans et regeneration du rapport, pilotes depuis /scan-report.
 *
 * IIFE autonome, style ES5 du projet (var / function, pas de fleche), avec ses
 * propres helpers HTTP. Le flux d'import est en deux temps : `analyze` rend un
 * apercu sans rien ecrire, `commit` applique le mapping corrige.
 *
 * Les envois multipart ne posent QUE l'en-tete X-CSRFToken : laisser le
 * navigateur fixer le Content-Type est indispensable pour que la frontiere
 * multipart soit correcte (meme convention que field_admin.js).
 */
(function () {
  "use strict";

  var $ = function (sel) { return document.querySelector(sel); };

  function csrf() {
    var m = document.querySelector('meta[name="csrf-token"]');
    return m ? m.getAttribute("content") : "";
  }

  function apiGet(url) {
    return fetch(url, { credentials: "same-origin" }).then(function (r) {
      return r.json();
    });
  }

  function apiPostJson(url, data) {
    return fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
      body: JSON.stringify(data || {})
    }).then(function (r) {
      return r.json().then(function (body) { return body; });
    });
  }

  // Etat de l'import en cours.
  var state = {
    token: null,
    units: [],
    overrides: {},   // "kind|name" -> {_id_feature, feature_collection}
    featureCache: {} // collection -> [items]
  };

  function currentEvent() {
    var el = document.getElementById("event-select");
    return el && el.value ? el.value : "";
  }
  function currentYear() {
    var el = document.getElementById("year-select");
    return el && el.value ? el.value : "";
  }

  function fmt(n) {
    return String(n == null ? 0 : n).replace(/\B(?=(\d{3})+(?!\d))/g, " ");
  }

  /**
   * Echappe une valeur avant interpolation dans du HTML.
   *
   * Indispensable : les noms d'unites viennent des colonnes du classeur
   * televerse, donc d'une source non maitrisee. Un fichier contenant une zone
   * nommee `<img src=x onerror=...>` executerait sinon du script dans une page
   * reservee aux administrateurs.
   */
  function esc(v) {
    return String(v == null ? "" : v)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  // `html` doit etre du balisage construit par ce fichier ; toute valeur
  // dynamique qui y est interpolee passe imperativement par esc().
  function note(cls, html) {
    var d = document.createElement("div");
    d.className = "scan-note " + cls;
    d.innerHTML = html;
    return d;
  }

  function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); }

  function showStep(step) {
    $("#scan-step-file").hidden = step !== "file";
    $("#scan-step-preview").hidden = step !== "preview";
    $("#scan-step-result").hidden = step !== "result";
    $("#scan-commit-btn").hidden = step !== "preview";
  }

  // ---- Etape 1 : envoi du fichier -----------------------------------------

  function openImport() {
    state.token = null;
    state.units = [];
    state.overrides = {};
    $("#scan-import-title").textContent =
      "Importer des scans — " + currentEvent() + " " + currentYear();
    $("#scan-file-input").value = "";
    $("#scan-upload-progress").hidden = true;
    clear($("#scan-result-body"));
    showStep("file");
    $("#scan-import-modal").hidden = false;
  }

  function upload(file) {
    if (!file) return;
    var fd = new FormData();
    fd.append("file", file);
    fd.append("event", currentEvent());
    fd.append("year", currentYear());

    var prog = $("#scan-upload-progress");
    var fill = prog.querySelector(".scan-progress-fill");
    var pct = $("#scan-upload-pct");
    var label = $("#scan-upload-label");
    prog.hidden = false;
    prog.querySelector(".scan-progress-bar").classList.remove("indeterminate");
    fill.style.width = "0%";
    label.textContent = "Envoi de " + file.name;

    var xhr = new XMLHttpRequest();
    xhr.open("POST", "/scan-report/import/analyze");
    xhr.setRequestHeader("X-CSRFToken", csrf());
    xhr.withCredentials = true;

    xhr.upload.onprogress = function (e) {
      if (!e.lengthComputable) return;
      var p = Math.round((e.loaded / e.total) * 100);
      fill.style.width = p + "%";
      pct.textContent = p + " %";
      if (p >= 100) {
        // Le parsing serveur prend quelques secondes : on bascule en
        // indetermine plutot que de laisser la barre figee a 100 %.
        label.textContent = "Analyse du classeur...";
        pct.textContent = "";
        prog.querySelector(".scan-progress-bar").classList.add("indeterminate");
      }
    };

    xhr.onload = function () {
      prog.hidden = true;
      prog.querySelector(".scan-progress-bar").classList.remove("indeterminate");
      var data;
      try { data = JSON.parse(xhr.responseText); }
      catch (e) { data = { ok: false, error: "reponse_illisible" }; }
      if (!data.ok) return failFile(data);
      renderPreview(data);
    };
    xhr.onerror = function () {
      prog.hidden = true;
      failFile({ error: "reseau_indisponible" });
    };
    xhr.send(fd);
  }

  var ERRORS = {
    fichier_manquant: "Aucun fichier recu.",
    extension_invalide: "Format non accepte : seuls les .xlsx et .xlsm le sont.",
    fichier_vide: "Le fichier est vide.",
    fichier_trop_volumineux: "Fichier trop volumineux (25 Mo maximum).",
    xlsx_illisible: "Classeur illisible ou corrompu.",
    xlsx_trop_court: "Le classeur ne contient pas de lignes de donnees.",
    xlsx_sans_colonnes_scan: "Aucune colonne Entree/Sortie/Autre trouvee en ligne 2.",
    xlsx_sans_donnees: "Aucune unite de scan exploitable.",
    xlsx_sans_creneaux: "Aucun creneau horaire exploitable.",
    parsing_impossible: "Le classeur n'a pas pu etre analyse.",
    annee_invalide: "Annee invalide.",
    import_expire: "Session d'import expiree, recommencer le depot du fichier.",
    document_trop_volumineux: "Volume de donnees trop important pour un seul document.",
    ecriture_partielle: "L'ecriture s'est interrompue en cours.",
    reseau_indisponible: "Serveur injoignable.",
    reponse_illisible: "Reponse du serveur illisible.",
    // Generation du rapport
    generation_en_cours: "Une generation est deja en cours pour ce couple.",
    generation_impossible: "La generation du rapport a echoue.",
    job_inconnu: "Tache inconnue ou expiree.",
    // Analyse
    cle_api_absente:
      "Cle API Anthropic non configuree sur le serveur : l'analyse redigee " +
      "n'est pas disponible.",
    analyse_en_cours: "Une analyse est deja en cours pour ce couple.",
    analyse_impossible: "L'analyse a echoue.",
    analyse_inconnue: "Analyse introuvable.",
    // Divers
    collection_inconnue: "Collection cartographique inconnue.",
    template_indisponible: "Le modele n'a pas pu etre genere."
  };

  function msg(data) {
    var base = ERRORS[data.error] || ("Erreur : " + esc(data.error || "inconnue"));
    return data.detail ? base + " (" + esc(data.detail) + ")" : base;
  }

  function failFile(data) {
    var host = $("#scan-step-file");
    var old = host.querySelector(".scan-note.err");
    if (old) old.remove();
    host.insertBefore(note("err", msg(data)), host.firstChild);
  }

  // ---- Etape 2 : apercu et mapping ----------------------------------------

  function renderPreview(data) {
    state.token = data.token;
    state.units = data.units;
    state.overrides = {};

    var s = data.summary;
    var notes = $("#scan-preview-notes");
    clear(notes);

    var existing = (data.existing && data.existing.types) || [];
    if (existing.length) {
      notes.appendChild(note("warn",
        "<strong>" + existing.length + " document(s) existent deja</strong> pour " +
        esc(data.event) + " " + esc(data.year) + " (" +
        esc(existing.map(function (t) { return t.type; }).join(", ")) +
        "). Ils seront archives dans <code>historique_controle_archive</code> " +
        "puis remplaces — l'ancien restera consultable."));
    }
    if (data.needs_confirm) {
      notes.appendChild(note("err",
        "<strong>Ecart de " + data.delta_pct + " %</strong> sur le cumul d'entrees " +
        "par rapport au document actuel (" + fmt(data.existing.frequentation_total_entree) +
        " → " + fmt(s.enceinte_entree) + "). Ce document sert de reference N-1 " +
        "aux comparaisons du cockpit : verifier qu'il s'agit bien du bon export."));
    }
    if (s.has_autre) {
      notes.appendChild(note("info",
        "Le fichier contient <strong>" + fmt(s.totals.autre) + " scans sans direction</strong> " +
        "(boitiers non configures en entree/sortie). Ils sont comptes dans le total " +
        "de passages de chaque porte mais exclus du calcul des presents."));
    }

    var tiles = [
      ["Periode", s.period_start.replace("T", " ") + " → " + s.period_end.replace("T", " ")],
      ["Creneaux", fmt(s.slot_count)],
      ["Zones", s.zone_count],
      ["Portes", s.porte_count],
      ["Entrees", fmt(s.totals.entree)],
      ["Sorties", fmt(s.totals.sortie)],
      ["Sans direction", fmt(s.totals.autre)]
    ];
    var host = $("#scan-preview-tiles");
    clear(host);
    tiles.forEach(function (t) {
      var d = document.createElement("div");
      d.className = "scan-tile";
      d.innerHTML = '<div class="k"></div><div class="v"></div>';
      d.querySelector(".k").textContent = t[0];
      d.querySelector(".v").textContent = t[1];
      host.appendChild(d);
    });

    $("#scan-race").value = data.race || "";

    var unresolved = state.units.filter(function (u) { return !u._id_feature; });
    $("#scan-unresolved-block").hidden = unresolved.length === 0;
    if (unresolved.length) {
      $("#scan-unresolved-count").textContent =
        unresolved.length + " unite(s) non localisee(s)";
      renderMapRows(unresolved);
    }

    showStep("preview");
  }

  function renderMapRows(units) {
    var host = $("#scan-map-rows");
    clear(host);
    units.forEach(function (u) {
      var key = u.kind + "|" + u.name;
      var row = document.createElement("div");
      row.className = "scanmap-row";

      var left = document.createElement("div");
      left.innerHTML = '<div class="scanmap-name"></div><div class="scanmap-sub"></div>';
      left.querySelector(".scanmap-name").textContent = u.name;
      left.querySelector(".scanmap-sub").textContent =
        (u.kind === "porte" ? "porte" : "zone") +
        (u.zone && u.zone !== u.name ? " · " + u.zone : "") +
        " · " + fmt(u.total_entree + u.total_sortie + u.total_autre) + " scans";

      var sel = document.createElement("select");
      ["portes", "terrains", "hospitalites", "tribunes"].forEach(function (c) {
        var o = document.createElement("option");
        o.value = c; o.textContent = c;
        if (c === (u.kind === "porte" ? "portes" : "terrains")) o.selected = true;
        sel.appendChild(o);
      });

      var right = document.createElement("div");
      var input = document.createElement("input");
      input.type = "text";
      input.setAttribute("list", "scanlist-" + key.replace(/[^a-z0-9]/gi, ""));
      input.placeholder = "Rechercher une entite...";
      var datalist = document.createElement("datalist");
      datalist.id = input.getAttribute("list");
      var chips = document.createElement("div");
      chips.className = "scanmap-chips";
      right.appendChild(input);
      right.appendChild(datalist);
      right.appendChild(chips);

      // Les suggestions du serveur couvrent le cas courant en un clic :
      // 'PORTE NORD' propose PORTE NORD PIETONS / VL / BIS.
      (u.suggestions || []).concat(u.candidates || []).forEach(function (c) {
        var chip = document.createElement("span");
        chip.className = "scanmap-chip";
        chip.textContent = c.label;
        chip.title = c.collection;
        chip.addEventListener("click", function () {
          setOverride(key, c._id_feature, c.collection);
          input.value = c.label;
          sel.value = c.collection;
          chips.querySelectorAll(".scanmap-chip").forEach(function (x) {
            x.classList.remove("is-set");
          });
          chip.classList.add("is-set");
        });
        chips.appendChild(chip);
      });

      function loadList() {
        var coll = sel.value;
        fetchFeatures(coll).then(function (items) {
          clear(datalist);
          items.forEach(function (it) {
            var o = document.createElement("option");
            o.value = it.label + (it.detail ? " — " + it.detail : "");
            o.dataset.id = it._id_feature;
            datalist.appendChild(o);
          });
        });
      }
      sel.addEventListener("change", function () {
        input.value = "";
        delete state.overrides[key];
        loadList();
      });
      input.addEventListener("change", function () {
        var opts = datalist.querySelectorAll("option");
        for (var i = 0; i < opts.length; i++) {
          if (opts[i].value === input.value) {
            setOverride(key, opts[i].dataset.id, sel.value);
            return;
          }
        }
        delete state.overrides[key];
      });
      loadList();

      row.appendChild(left);
      row.appendChild(sel);
      row.appendChild(right);
      host.appendChild(row);
    });
  }

  function setOverride(key, id, collection) {
    state.overrides[key] = { _id_feature: id, feature_collection: collection };
  }

  function fetchFeatures(collection) {
    if (state.featureCache[collection]) {
      return Promise.resolve(state.featureCache[collection]);
    }
    // Les collections font au plus 156 entites : un seul chargement puis
    // filtrage local, plutot qu'une requete par frappe.
    return apiGet("/scan-report/features?collection=" + encodeURIComponent(collection))
      .then(function (d) {
        var items = (d && d.items) || [];
        state.featureCache[collection] = items;
        return items;
      })
      .catch(function () { return []; });
  }

  // ---- Etape 3 : ecriture --------------------------------------------------

  function commit() {
    var payload = {
      token: state.token,
      event: currentEvent(),
      year: parseInt(currentYear(), 10),
      race: $("#scan-race").value || null,
      save_overrides: $("#scan-save-overrides").checked,
      overrides: {}
    };
    Object.keys(state.overrides).forEach(function (k) {
      payload.overrides[k] = {
        _id_feature: state.overrides[k]._id_feature,
        feature_collection: state.overrides[k].feature_collection,
        save: payload.save_overrides
      };
    });

    showStep("result");
    $("#scan-commit-progress").hidden = false;
    clear($("#scan-result-body"));

    apiPostJson("/scan-report/import/commit", payload).then(function (d) {
      $("#scan-commit-progress").hidden = true;
      var body = $("#scan-result-body");
      if (!d.ok) {
        body.appendChild(note("err", msg(d)));
        if (d.written && d.written.length) {
          body.appendChild(note("warn",
            "Documents deja ecrits avant l'interruption : <strong>" +
            esc(d.written.join(", ")) + "</strong>. Les archives correspondantes " +
            "permettent de revenir en arriere."));
        }
        return;
      }
      var lines = ["<strong>Import termine.</strong>",
        "Documents ecrits : " + esc(d.written.join(", ")) + "."];
      if (d.archived && d.archived.length) {
        lines.push("Archives : " + esc(d.archived.map(function (a) {
          return a.type;
        }).join(", ")) + ".");
      }
      if (d.overrides_saved) {
        lines.push(d.overrides_saved + " rattachement(s) memorise(s).");
      }
      body.appendChild(note("ok", lines.join("<br>")));
      if (d.unresolved && d.unresolved.length) {
        body.appendChild(note("warn",
          "Non localisees (aucune entite cartographique) : <strong>" +
          esc(d.unresolved.map(function (u) { return u.name; }).join(", ")) +
          "</strong>. Les donnees sont importees, seule la localisation manque."));
      }
      body.appendChild(note("info",
        "Etape suivante : regenerer le rapport avec le bouton " +
        "<span class=\"material-symbols-outlined\" style=\"font-size:14px;vertical-align:-2px;\">refresh</span> " +
        "du bandeau. Les effectifs sont calcules a ce moment-la, depuis le " +
        "calendrier — rien a deposer."));
    }).catch(function () {
      $("#scan-commit-progress").hidden = true;
      $("#scan-result-body").appendChild(note("err", ERRORS.reseau_indisponible));
    });
  }

  // ---- Regeneration --------------------------------------------------------

  function openRegen() {
    var body = $("#scan-regen-body");
    clear(body);
    $("#scan-regen-progress").hidden = true;
    $("#scan-regen-go").disabled = false;
    body.appendChild(note("info",
      "Regenere le rapport HTML de <strong>" + esc(currentEvent()) + " " +
      esc(currentYear()) + "</strong> a partir des donnees en base."));
    $("#scan-regen-modal").hidden = false;
  }

  function runRegen() {
    var btn = $("#scan-regen-go");
    btn.disabled = true;
    var prog = $("#scan-regen-progress");
    var fill = prog.querySelector(".scan-progress-fill");
    prog.hidden = false;
    fill.style.width = "0%";
    $("#scan-regen-label").textContent = "Demarrage...";

    apiPostJson("/scan-report/generate", {
      event: currentEvent(), year: parseInt(currentYear(), 10)
    }).then(function (d) {
      if (!d.ok) {
        prog.hidden = true;
        btn.disabled = false;
        $("#scan-regen-body").appendChild(note("err", msg(d)));
        return;
      }
      poll(d.job_id, fill);
    }).catch(function () {
      prog.hidden = true;
      btn.disabled = false;
      $("#scan-regen-body").appendChild(note("err", ERRORS.reseau_indisponible));
    });
  }

  function poll(jobId, fill) {
    apiGet("/scan-report/generate/status?job=" + encodeURIComponent(jobId))
      .then(function (d) {
        var pct = d.progress || 0;
        fill.style.width = pct + "%";
        $("#scan-regen-pct").textContent = pct + " %";
        $("#scan-regen-label").textContent = d.step || d.status || "";
        if (d.status === "running" || d.status === "queued") {
          setTimeout(function () { poll(jobId, fill); }, 1500);
          return;
        }
        $("#scan-regen-go").disabled = false;
        if (d.status === "done") {
          var r = d.result || {};
          var lines = ["<strong>Rapport regenere.</strong>",
            esc(r.zones || 0) + " zone(s), " + esc(r.portes || 0) +
            " porte(s) — " + esc(r.size_kb || 0) + " Ko."];
          if (r.source === "legacy") {
            lines.push("Source : ancienne chaine (aucun document " +
                       "<code>complet</code> importe pour ce couple).");
          }
          $("#scan-regen-body").appendChild(note("ok", lines.join("<br>")));

          // Ce que le rapport ne montre pas doit etre dit : le gabarit n'a
          // que deux series, les scans sans direction n'y figurent nulle part.
          if (r.autre_scans_not_shown) {
            $("#scan-regen-body").appendChild(note("info",
              esc(fmt(r.autre_scans_not_shown)) + " scans sans direction ne " +
              "figurent pas dans le rapport : le gabarit n'affiche que les " +
              "entrees et les sorties. Ils restent dans le document " +
              "<code>complet</code>."));
          }
          if (r.omitted_units && r.omitted_units.length) {
            $("#scan-regen-body").appendChild(note("warn",
              "Unite(s) sans aucun flux dirige, ecartee(s) du rapport : " +
              "<strong>" + esc(r.omitted_units.join(", ")) + "</strong>."));
          }
          var iframe = document.querySelector(".scan-report-iframe");
          if (iframe) {
            // Cache-buster : sans lui le navigateur ressert l'ancien rapport
            // et l'utilisateur croit que rien ne s'est passe.
            var base = iframe.getAttribute("src").split("&_t=")[0];
            iframe.setAttribute("src", base + "&_t=" + Date.now());
          } else {
            setTimeout(function () { location.reload(); }, 900);
          }
        } else {
          $("#scan-regen-body").appendChild(note("err",
            d.error ? msg({ error: d.error, detail: d.detail }) : "Echec de la generation."));
        }
      })
      .catch(function () {
        setTimeout(function () { poll(jobId, fill); }, 3000);
      });
  }

  // ---- Analyse redigee -----------------------------------------------------

  var SECTION_LABELS = {
    synthese: "Synthese",
    pics_et_saturation: "Pics et saturation",
    portes_critiques: "Portes critiques",
    zones_critiques: "Zones critiques",
    comparaison_n1: "Comparaison N-1",
    anomalies: "Anomalies et limites",
    recommandations: "Recommandations"
  };

  function openAnalysis() {
    $("#scan-analysis-title").textContent =
      "Analyse redigee — " + currentEvent() + " " + currentYear();
    clear($("#scan-analysis-head"));
    clear($("#scan-analysis-body"));
    $("#scan-analysis-progress").hidden = true;
    $("#scan-analysis-go").disabled = false;
    $("#scan-analysis-modal").hidden = false;
    loadLastAnalysis();
  }

  function loadLastAnalysis() {
    apiGet("/scan-report/analysis/list?event=" + encodeURIComponent(currentEvent()) +
           "&year=" + encodeURIComponent(currentYear()))
      .then(function (d) {
        var head = $("#scan-analysis-head");
        if (!d.ok || !d.items || !d.items.length) {
          head.appendChild(note("info",
            "Aucune analyse pour ce couple. La generation lit les indicateurs " +
            "du document <code>complet</code> et les soumet au modele."));
          return;
        }
        head.appendChild(note("info",
          esc(d.items.length) + " analyse(s) en historique. La plus recente : " +
          esc(d.items[0].created_at) + " (" + esc(d.items[0].model) + ")."));
        showAnalysis(d.items[0]._id);
      })
      .catch(function () {});
  }

  function showAnalysis(id) {
    apiGet("/scan-report/analysis/" + encodeURIComponent(id)).then(function (d) {
      if (!d.ok) return;
      renderSections(d.analysis);
    }).catch(function () {});
  }

  function renderSections(a) {
    var body = $("#scan-analysis-body");
    clear(body);
    var sections = a.sections;
    if (!sections) {
      // Reponse non parsable : on montre le texte brut plutot que rien.
      body.appendChild(note("warn",
        "Reponse non structuree, affichage du texte brut."));
      var pre = document.createElement("pre");
      pre.style.cssText = "white-space:pre-wrap;font-size:12.5px;color:#c8d6ea;";
      pre.textContent = a.raw_text || "";
      body.appendChild(pre);
      return;
    }
    Object.keys(SECTION_LABELS).forEach(function (key) {
      var val = sections[key];
      if (!val) return;
      var h = document.createElement("div");
      h.style.cssText = "font-size:11px;text-transform:uppercase;letter-spacing:.6px;" +
                        "color:#4f9eff;margin:16px 0 6px;font-weight:600;";
      h.textContent = SECTION_LABELS[key];
      var p = document.createElement("div");
      p.style.cssText = "font-size:13px;line-height:1.65;color:#c8d6ea;white-space:pre-wrap;";
      p.textContent = val;
      body.appendChild(h);
      body.appendChild(p);
    });
    if (a.usage) {
      var u = document.createElement("div");
      u.style.cssText = "margin-top:18px;padding-top:10px;border-top:1px solid #232d3f;" +
                        "font-size:11px;color:#8aa0bd;";
      u.textContent = "Modele " + (a.model || "?") + " — " +
        fmt(a.usage.input_tokens || 0) + " tokens en entree, " +
        fmt(a.usage.output_tokens || 0) + " en sortie" +
        (a.usage.cache_read_input_tokens
          ? " (" + fmt(a.usage.cache_read_input_tokens) + " lus en cache)" : "");
      body.appendChild(u);
    }
  }

  function runAnalysis() {
    var btn = $("#scan-analysis-go");
    btn.disabled = true;
    clear($("#scan-analysis-body"));
    var prog = $("#scan-analysis-progress");
    var fill = prog.querySelector(".scan-progress-fill");
    prog.hidden = false;
    fill.style.width = "0%";
    $("#scan-analysis-step").textContent = "Demarrage...";

    apiPostJson("/scan-report/analysis/generate", {
      event: currentEvent(), year: parseInt(currentYear(), 10)
    }).then(function (d) {
      if (!d.ok) {
        prog.hidden = true;
        btn.disabled = false;
        $("#scan-analysis-body").appendChild(note("err", msg(d)));
        return;
      }
      pollAnalysis(d.job_id, fill, btn);
    }).catch(function () {
      prog.hidden = true;
      btn.disabled = false;
      $("#scan-analysis-body").appendChild(note("err", ERRORS.reseau_indisponible));
    });
  }

  function pollAnalysis(jobId, fill, btn) {
    apiGet("/scan-report/generate/status?job=" + encodeURIComponent(jobId))
      .then(function (d) {
        var pct = d.progress || 0;
        fill.style.width = pct + "%";
        $("#scan-analysis-pct").textContent = pct + " %";
        $("#scan-analysis-step").textContent = d.step || d.status || "";
        if (d.status === "running" || d.status === "queued") {
          setTimeout(function () { pollAnalysis(jobId, fill, btn); }, 2000);
          return;
        }
        btn.disabled = false;
        $("#scan-analysis-progress").hidden = true;
        if (d.status === "done" && d.result && d.result.id) {
          showAnalysis(d.result.id);
        } else {
          $("#scan-analysis-body").appendChild(note("err",
            msg({ error: d.error, detail: d.detail })));
        }
      })
      .catch(function () {
        setTimeout(function () { pollAnalysis(jobId, fill, btn); }, 3000);
      });
  }

  function showHistory() {
    apiGet("/scan-report/analysis/list?event=" + encodeURIComponent(currentEvent()) +
           "&year=" + encodeURIComponent(currentYear()))
      .then(function (d) {
        var body = $("#scan-analysis-body");
        clear(body);
        if (!d.ok || !d.items || !d.items.length) {
          body.appendChild(note("info", "Aucune analyse enregistree."));
          return;
        }
        d.items.forEach(function (it) {
          var row = document.createElement("div");
          row.style.cssText = "display:flex;justify-content:space-between;align-items:center;" +
                              "padding:9px 0;border-bottom:1px solid #232d3f;font-size:12.5px;";
          var left = document.createElement("span");
          left.textContent = it.created_at + "  ·  " + (it.model || "?") +
                             (it.created_by ? "  ·  " + it.created_by : "");
          left.style.color = "#c8d6ea";
          var open = document.createElement("button");
          open.className = "btn btn-secondary";
          open.style.cssText = "padding:3px 11px;font-size:12px;";
          open.textContent = "Ouvrir";
          open.addEventListener("click", function () { showAnalysis(it._id); });
          row.appendChild(left);
          row.appendChild(open);
          body.appendChild(row);
        });
      })
      .catch(function () {});
  }

  // ---- Cablage -------------------------------------------------------------

  document.addEventListener("DOMContentLoaded", function () {
    var importBtn = $("#scan-import-btn");
    var regenBtn = $("#scan-regen-btn");
    if (importBtn) importBtn.addEventListener("click", openImport);
    if (regenBtn) regenBtn.addEventListener("click", openRegen);

    var drop = $("#scan-drop");
    var input = $("#scan-file-input");
    if (drop && input) {
      drop.addEventListener("click", function () { input.click(); });
      input.addEventListener("change", function () { upload(input.files[0]); });
      ["dragenter", "dragover"].forEach(function (ev) {
        drop.addEventListener(ev, function (e) {
          e.preventDefault(); drop.classList.add("dragover");
        });
      });
      ["dragleave", "drop"].forEach(function (ev) {
        drop.addEventListener(ev, function (e) {
          e.preventDefault(); drop.classList.remove("dragover");
        });
      });
      drop.addEventListener("drop", function (e) {
        if (e.dataTransfer && e.dataTransfer.files.length) {
          upload(e.dataTransfer.files[0]);
        }
      });
    }

    var commitBtn = $("#scan-commit-btn");
    if (commitBtn) commitBtn.addEventListener("click", commit);
    var regenGo = $("#scan-regen-go");
    if (regenGo) regenGo.addEventListener("click", runRegen);

    var analysisBtn = $("#scan-analysis-btn");
    if (analysisBtn) analysisBtn.addEventListener("click", openAnalysis);
    var analysisGo = $("#scan-analysis-go");
    if (analysisGo) analysisGo.addEventListener("click", runAnalysis);
    var analysisHist = $("#scan-analysis-history");
    if (analysisHist) analysisHist.addEventListener("click", showHistory);

    document.querySelectorAll(".crud-modal [data-close]").forEach(function (el) {
      el.addEventListener("click", function () {
        var modal = el.closest(".crud-modal");
        if (!modal) return;
        modal.hidden = true;
        // Abandon d'un import en cours : on libere le fichier en attente
        // cote serveur plutot que d'attendre l'expiration.
        if (modal.id === "scan-import-modal" && state.token) {
          fetch("/scan-report/import/" + state.token, {
            method: "DELETE",
            credentials: "same-origin",
            headers: { "X-CSRFToken": csrf() }
          }).catch(function () {});
          state.token = null;
        }
      });
    });
  });
})();
