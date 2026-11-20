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
  // Horodatage du lancement d'une regeneration, pour afficher le temps ecoule.
  var regenStartedAt = null;

  // Deux contextes pour la meme table de mapping : celle de l'import (avant
  // ecriture) et celle de la correction apres coup (sur le document `complet`).
  // Meme rendu, memes interactions, cibles DOM et stockage differents.
  var importCtx = {
    host: "#scan-map-rows", recap: "#scan-ignore-recap",
    units: [], overrides: {}, categories: [],
  };
  var mappingCtx = {
    host: "#scan-mapping-rows", recap: "#scan-mapping-recap",
    units: [], overrides: {}, categories: [],
  };

  var state = {
    token: null,
    imported: null,  // couple (event, year) du dernier import reussi
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
    clear($("#scan-format-fix"));
    var oldErr = $("#scan-step-file").querySelector(".scan-note.err");
    if (oldErr) oldErr.remove();
    clear($("#scan-result-body"));
    showStep("file");
    $("#scan-import-modal").hidden = false;
  }

  // Aligne sur MAX_UPLOAD_BYTES (scan_report.py) et sur la limite IIS du
  // web.config : refuser cote client evite d'envoyer 300 Mo pour rien.
  var MAX_UPLOAD_MB = 200;

  function upload(file) {
    if (!file) return;
    if (file.size > MAX_UPLOAD_MB * 1024 * 1024) {
      return failFile({ error: "fichier_trop_volumineux" });
    }
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
        label.textContent = "Fichier recu, analyse...";
        pct.textContent = "";
        prog.querySelector(".scan-progress-bar").classList.add("indeterminate");
      }
    };

    xhr.onload = function () {
      var data;
      try { data = JSON.parse(xhr.responseText); }
      catch (e) {
        // IIS rejette un corps trop gros en 404.13 / 413, en HTML, avant
        // meme que la requete n'atteigne l'application.
        data = { ok: false, error: (xhr.status === 404 || xhr.status === 413)
          ? "fichier_trop_volumineux" : "reponse_illisible" };
      }
      if (!data.ok) {
        prog.hidden = true;
        return failFile(data);
      }
      state.token = data.token;
      followImportJob(data.job_id);
    };
    xhr.onerror = function () {
      prog.hidden = true;
      failFile({ error: "reseau_indisponible" });
    };
    xhr.send(fd);
  }

  /**
   * Suit l'analyse du fichier, faite en tache de fond par le serveur : un
   * journal de 750 000 lignes prend ~35 s, trop pres du delai du proxy IIS
   * pour tenir dans la requete d'envoi.
   */
  function followImportJob(jobId) {
    var prog = $("#scan-upload-progress");
    var bar = prog.querySelector(".scan-progress-bar");
    var fill = prog.querySelector(".scan-progress-fill");
    var pct = $("#scan-upload-pct");
    var label = $("#scan-upload-label");
    var startedAt = Date.now();
    prog.hidden = false;
    bar.classList.remove("indeterminate");

    function tick() {
      apiGet("/scan-report/generate/status?job=" + encodeURIComponent(jobId))
        .then(function (d) {
          if (!d.ok) {
            prog.hidden = true;
            return failFile(d);
          }
          var p = d.progress || 0;
          var sec = Math.round((Date.now() - startedAt) / 1000);
          fill.style.width = p + "%";
          pct.textContent = p + " %";
          label.textContent = (d.step || "Analyse") + (sec > 2 ? " — " + sec + " s" : "");
          if (d.status === "running" || d.status === "queued") {
            setTimeout(tick, 1000);
            return;
          }
          prog.hidden = true;
          if (d.status === "done") return renderPreview(d.result);
          // Colonnes non reconnues : le serveur renvoie ce qu'il a vu du
          // fichier, l'utilisateur complete le mapping sans le renvoyer.
          var r = d.result || {};
          failFile({ error: d.error, detail: d.detail });
          if (r.format && r.format.columns && r.format.columns.length) {
            showStep("file");
            renderFormatBlock($("#scan-format-fix"), r.format, true);
          }
        })
        .catch(function () {
          setTimeout(tick, 3000);
        });
    }
    tick();
  }

  // ---- Format du fichier et colonnes ---------------------------------------

  var SENS_CHOICES = [["e", "Entree"], ["s", "Sortie"], ["a", "Sans direction"],
                      ["x", "Exclure"]];
  // Hors presents : passage compte a la porte, mais ni entree ni present
  // (un scan de vehicule ne dit pas combien de personnes sont a bord).
  var FAMILY_CHOICES = [["p", "Personnes (presents)"], ["h", "Hors presents (vehicules)"],
                        ["x", "Exclure de l'import"]];

  /**
   * Bloc « format lu » : type de fichier, chiffres de lecture et, pour un
   * journal, le choix des colonnes et des options, modifiable puis relu par
   * le serveur sans reteleverser le fichier.
   */
  function renderFormatBlock(host, fmtInfo, open) {
    clear(host);
    if (!fmtInfo) return;
    var box = document.createElement("details");
    box.className = "scan-format";
    if (open) box.open = true;
    var sum = document.createElement("summary");
    var st = fmtInfo.stats || {};
    var bits = [fmtInfo.label];
    if (st.lignes != null) {
      bits.push(fmt(st.lignes) + " lignes lues, " + fmt(st.retenues || 0) + " retenues");
    }
    sum.textContent = bits.join(" — ");
    box.appendChild(sum);

    if (fmtInfo.id !== "journal") {
      host.appendChild(box);
      return;
    }

    var details = [];
    if (st.doublons) details.push(fmt(st.doublons) + " doublon(s) stricts retires");
    if (st.refuses) details.push(fmt(st.refuses) + " scan(s) refuse(s) ecarte(s)");
    if (st.hors_presents) details.push(fmt(st.hors_presents) + " passage(s) hors presents (vehicules)");
    if (st.exclus_famille) details.push(fmt(st.exclus_famille) + " ligne(s) exclue(s) par famille de titre");
    if (st.exclus_sens) details.push(fmt(st.exclus_sens) + " ligne(s) exclue(s) par le sens");
    if (st.sans_date) details.push(fmt(st.sans_date) + " ligne(s) sans date lisible");
    if (st.sans_point) details.push(fmt(st.sans_point) + " ligne(s) sans point de controle");
    if (st.precision_horaire) details.push(fmt(st.precision_horaire) + " ligne(s) a l'heure pres seulement");
    if (details.length) {
      var p = document.createElement("div");
      p.className = "scan-format-stats";
      p.textContent = details.join(" · ");
      box.appendChild(p);
    }
    if ((fmtInfo.warnings || []).indexOf("sens_absent") >= 0) {
      box.appendChild(note("warn", "Aucune colonne de sens reconnue : sans entrees ni " +
        "sorties, aucun present ne pourra etre calcule."));
    }

    // Choix des colonnes, role par role.
    var grid = document.createElement("div");
    grid.className = "scan-format-grid";
    var selects = {};
    (fmtInfo.roles || []).forEach(function (role) {
      var lab = document.createElement("label");
      var t = document.createElement("span");
      t.textContent = role.label + (role.required ? " *" : "");
      var sel = document.createElement("select");
      var none = document.createElement("option");
      none.value = "";
      none.textContent = "(aucune)";
      sel.appendChild(none);
      (fmtInfo.columns || []).forEach(function (c) {
        var o = document.createElement("option");
        o.value = String(c.index);
        o.textContent = c.header + (c.sample && c.sample.length
          ? " — ex. " + c.sample.slice(0, 2).join(" / ") : "");
        if (fmtInfo.mapping && fmtInfo.mapping[role.id] === c.index) o.selected = true;
        sel.appendChild(o);
      });
      selects[role.id] = sel;
      lab.appendChild(t);
      lab.appendChild(sel);
      grid.appendChild(lab);
    });
    box.appendChild(grid);

    // Options de lecture.
    var opts = fmtInfo.options || {};
    var optBox = document.createElement("div");
    optBox.className = "scan-format-opts";
    var checks = {};
    [["strip_tags", "Retirer les etiquettes entre crochets des noms ([GPE25]...)"],
     ["dedup", "Retirer les doublons stricts (meme billet, boitier, seconde et sens)"],
     ["day_first", "Dates au format jour/mois (decocher pour mois/jour)"]]
      .forEach(function (o) {
        var lab = document.createElement("label");
        lab.className = "scan-check";
        var cb = document.createElement("input");
        cb.type = "checkbox";
        cb.checked = opts[o[0]] !== false && (o[0] !== "dedup" || !!opts.dedup);
        var sp = document.createElement("span");
        sp.textContent = o[1];
        lab.appendChild(cb);
        lab.appendChild(sp);
        optBox.appendChild(lab);
        checks[o[0]] = cb;
      });
    box.appendChild(optBox);

    // Valeurs du sens et du resultat : ce que le serveur en a compris,
    // corrigeable valeur par valeur (IN/OUT, 1/0, libelles maison...).
    var sensSel = {};
    if ((fmtInfo.sens_values || []).length) {
      box.appendChild(subTitle("Valeurs du sens"));
      var sg = document.createElement("div");
      sg.className = "scan-format-values";
      fmtInfo.sens_values.forEach(function (v) {
        var lab = document.createElement("label");
        var t = document.createElement("span");
        t.textContent = (v.label || "(vide)") + " — " + fmt(v.count);
        var sel = document.createElement("select");
        SENS_CHOICES.forEach(function (c) {
          var o = document.createElement("option");
          o.value = c[0];
          o.textContent = c[1];
          if (c[0] === v.map) o.selected = true;
          sel.appendChild(o);
        });
        sensSel[v.value] = sel;
        lab.appendChild(t);
        lab.appendChild(sel);
        sg.appendChild(lab);
      });
      box.appendChild(sg);
    }
    // Familles de titre : seules les personnes font des presents.
    var famSel = {};
    if ((fmtInfo.famille_values || []).length) {
      box.appendChild(subTitle("Familles de titre — le pic de presents ne compte que les personnes"));
      var fg = document.createElement("div");
      fg.className = "scan-format-values";
      fmtInfo.famille_values.forEach(function (v) {
        var lab = document.createElement("label");
        var t = document.createElement("span");
        t.textContent = (v.label || "(vide)") + " — " + fmt(v.count);
        var sel = document.createElement("select");
        FAMILY_CHOICES.forEach(function (c) {
          var o = document.createElement("option");
          o.value = c[0];
          o.textContent = c[1];
          if (c[0] === v.map) o.selected = true;
          sel.appendChild(o);
        });
        famSel[v.value] = sel;
        lab.appendChild(t);
        lab.appendChild(sel);
        fg.appendChild(lab);
      });
      box.appendChild(fg);
    }

    var resBox = {};
    if ((fmtInfo.result_values || []).length) {
      box.appendChild(subTitle("Resultats retenus"));
      var rg = document.createElement("div");
      rg.className = "scan-format-values";
      fmtInfo.result_values.forEach(function (v) {
        var lab = document.createElement("label");
        lab.className = "scan-check";
        var cb = document.createElement("input");
        cb.type = "checkbox";
        cb.checked = !!v.map;
        var sp = document.createElement("span");
        sp.textContent = (v.label || "(vide)") + " — " + fmt(v.count);
        lab.appendChild(cb);
        lab.appendChild(sp);
        resBox[v.value] = cb;
        rg.appendChild(lab);
      });
      box.appendChild(rg);
    }

    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn btn-secondary scan-format-go";
    btn.textContent = "Relire le fichier avec ces reglages";
    btn.addEventListener("click", function () {
      var mapping = {};
      Object.keys(selects).forEach(function (k) {
        mapping[k] = selects[k].value === "" ? null : parseInt(selects[k].value, 10);
      });
      var options = {
        strip_tags: checks.strip_tags.checked,
        dedup: checks.dedup.checked,
        day_first: checks.day_first.checked,
        sens_map: {}, result_map: {}, famille_map: {}
      };
      Object.keys(sensSel).forEach(function (k) { options.sens_map[k] = sensSel[k].value; });
      Object.keys(famSel).forEach(function (k) { options.famille_map[k] = famSel[k].value; });
      Object.keys(resBox).forEach(function (k) { options.result_map[k] = resBox[k].checked; });
      reanalyze(mapping, options, btn);
    });
    box.appendChild(btn);
    host.appendChild(box);
  }

  function subTitle(text) {
    var d = document.createElement("div");
    d.className = "scanmap-col-label";
    d.style.marginTop = "10px";
    d.textContent = text;
    return d;
  }

  function reanalyze(mapping, options, btn) {
    if (!state.token) return failFile({ error: "import_expire" });
    btn.disabled = true;
    var old = $("#scan-step-file").querySelector(".scan-note.err");
    if (old) old.remove();
    // La relecture se suit dans l'etape 1, ou vit la barre de progression.
    clear($("#scan-format-fix"));
    showStep("file");
    apiPostJson("/scan-report/import/reanalyze", {
      token: state.token, mapping: mapping, options: options
    }).then(function (d) {
      btn.disabled = false;
      if (!d.ok) return failFile(d);
      followImportJob(d.job_id);
    }).catch(function () {
      btn.disabled = false;
      failFile({ error: "reseau_indisponible" });
    });
  }

  var ERRORS = {
    fichier_manquant: "Aucun fichier recu.",
    extension_invalide: "Format non accepte : .xlsx, .xlsm, .csv ou .txt.",
    fichier_vide: "Le fichier est vide.",
    fichier_trop_volumineux: "Fichier trop volumineux (200 Mo maximum).",
    fichier_vide_ou_illisible: "Fichier vide ou illisible.",
    colonne_point_absente:
      "Colonne du point de controle (porte, tribune...) non reconnue : la choisir ci-dessous.",
    colonne_date_absente:
      "Colonne de date du scan non reconnue : la choisir ci-dessous.",
    matrice_csv_non_supportee:
      "Une matrice Sirius doit etre deposee au format .xlsx (cellules fusionnees).",
    analyse_fichier_en_cours: "L'analyse du fichier n'est pas terminee.",
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
    clear($("#scan-format-fix"));
    renderFormatBlock($("#scan-format-block"), data.format, false);
    if (data.kind_from_category) {
      notes.appendChild(note("info",
        "Journal de scans : rien dans le fichier ne dit quels points sont des " +
        "portes d'enceinte. La categorie <strong>Porte d'enceinte</strong> fait " +
        "entrer une unite dans la serie de l'enceinte generale (presents, " +
        "reference N-1) ; toute autre categorie en fait une zone."));
    }

    var existing = (data.existing && data.existing.types) || [];
    if (existing.length) {
      notes.appendChild(note("warn",
        "<strong>" + existing.length + " document(s) existent deja</strong> pour " +
        esc(data.event) + " " + esc(data.year) + " (" +
        esc(existing.map(function (t) { return t.type; }).join(", ")) +
        "). Ils seront archives dans <code>historique_controle_archive</code> " +
        "puis remplaces — l'ancien restera consultable."));
    }
    if (!data.race) {
      // Sans date de course, la vue Frequentation ignore l'edition (alignement
      // J-n impossible) : l'onglet resterait vide sans explication.
      notes.appendChild(note("err",
        "<strong>Aucune date de course trouvee</strong> pour " + esc(data.event) + " " +
        esc(data.year) + " (ni en base, ni dans les parametrages). La saisir " +
        "ci-dessous, sinon l'onglet Frequentation du rapport restera vide."));
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
        "(boitiers non configures en entree/sortie, ou titres classes hors presents " +
        "comme les vehicules). Ils sont comptes dans le total de passages de chaque " +
        "porte mais exclus des entrees et du calcul des presents."));
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
    // Remise a zero nocturne : reprise de la derniere edition de l'evenement.
    var rh = data.presence_reset_hour;
    $("#scan-reset-on").checked = rh !== null && rh !== undefined;
    $("#scan-reset-hour").value = (rh !== null && rh !== undefined) ? rh : 5;

    state.categories = data.categories || [];
    importCtx.units = state.units;
    importCtx.overrides = state.overrides;
    importCtx.categories = state.categories;
    importCtx.kindFromCategory = !!data.kind_from_category;
    var unresolved = state.units.filter(function (u) {
      return !u._id_feature && !u.no_location;
    });
    // Toutes les unites sont proposees a la verification, y compris celles
    // resolues automatiquement : une resolution auto peut se tromper, et la
    // categorie n'est qu'une proposition dans tous les cas.
    $("#scan-unresolved-block").hidden = state.units.length === 0;
    $("#scan-unresolved-count").textContent =
      state.units.length + " unite(s), dont " + unresolved.length +
      " a localiser";
    $("#scan-only-unresolved").checked = false;
    renderMapRows(importCtx);
    refreshIgnoreRecap(importCtx);

    showStep("preview");
  }

  // « Aucune collection » : decision explicite que l'unite n'est pas un lieu
  // (guichet, service, renfort mobile). A distinguer d'un rattachement encore
  // a faire, qui lui doit rester signale.
  function isNoLocation(ctx, u, key) {
    var ov = ctx.overrides[key];
    if (ov && ov.no_location != null) return !!ov.no_location;
    return !!u.no_location;
  }

  function isIgnored(ctx, u, key) {
    var ov = ctx.overrides[key];
    if (ov && ov.ignored != null) return !!ov.ignored;
    return !!u.ignored;
  }

  function rowState(ctx, u, key) {
    if (isIgnored(ctx, u, key)) return "is-ignored";
    if (isNoLocation(ctx, u, key)) return "is-nolocation";
    var ov = ctx.overrides[key];
    if (ov && (ov._id_feature || ov.category)) return "is-manual";
    if (!u._id_feature) return "is-none";
    // Un choix memorise lors d'un import precedent compte comme manuel, qu'il
    // porte sur l'entite ou sur la seule categorie.
    if (u.feature_source === "manuel" || u.category_source === "manuel") {
      return "is-manual";
    }
    return "is-auto";
  }

  // Cle des choix d'une unite. Le serveur la fournit pour un import (dans un
  // journal, `kind` suit la categorie et change en cours de saisie : la cle,
  // elle, ne doit pas bouger) ; l'editeur de mapping la reconstruit.
  function unitKey(u) {
    return u.key || (u.kind + "|" + u.name);
  }

  function subLabel(u) {
    return (u.kind === "porte" ? "porte" : "zone") +
      (u.zone && u.zone !== u.name ? " · " + u.zone : "") +
      " · " + fmt(u.total_entree + u.total_sortie + u.total_autre) + " scans" +
      (u.feature_label ? " · " + u.feature_label : "");
  }

  function renderMapRows(ctx) {
    var host = $(ctx.host);
    clear(host);
    ctx.units.forEach(function (u) {
      var key = unitKey(u);
      var noLoc = isNoLocation(ctx, u, key);
      var row = document.createElement("div");
      row.className = "scanmap-row " + rowState(ctx, u, key);
      // « sans lieu » est une question tranchee : le filtre « a localiser »
      // ne doit pas la reposer.
      row.dataset.resolved = (u._id_feature || noLoc) ? "1" : "0";

      var left = document.createElement("div");
      left.innerHTML = '<div class="scanmap-name"></div><div class="scanmap-sub"></div>';
      left.querySelector(".scanmap-name").textContent = u.name;
      left.querySelector(".scanmap-sub").textContent = subLabel(u);

      // Ecarter une unite : elle ne figurera dans aucun des trois documents.
      var ignLabel = document.createElement("label");
      // `scan-check` annule le flex-direction:column que .crud-modal-body
      // impose a tous les labels : sans elle, la case se retrouve empilee
      // au-dessus de son libelle.
      ignLabel.className = "scanmap-ignore scan-check";
      var ignBox = document.createElement("input");
      ignBox.type = "checkbox";
      ignBox.checked = isIgnored(ctx, u, key);
      var ignTxt = document.createElement("span");
      ignTxt.textContent = "Ignorer";
      ignLabel.appendChild(ignBox);
      ignLabel.appendChild(ignTxt);
      left.appendChild(ignLabel);

      // Categorie d'exploitation : pilote le regroupement du rapport et la
      // capacite retenue (pietons ou vehicules), donc l'effectif recommande.
      var catBox = document.createElement("div");
      catBox.appendChild(labelled("Categorie"));
      var catSel = document.createElement("select");
      (ctx.categories || []).forEach(function (c) {
        var o = document.createElement("option");
        o.value = c.id;
        o.textContent = c.label;
        if (c.id === u.category) o.selected = true;
        catSel.appendChild(o);
      });
      catSel.addEventListener("change", function () {
        setOverride(ctx, key, null, null, catSel.value);
        if (ctx.kindFromCategory) {
          // Journal : la categorie decide de la nature (porte d'enceinte ou
          // zone), le serveur appliquera le meme choix a l'ecriture.
          u.kind = catSel.value === "porte" ? "porte" : "zone";
          u.zone = u.kind === "porte" ? "ENCEINTE GENERALE" : u.name;
          left.querySelector(".scanmap-sub").textContent = subLabel(u);
          refreshIgnoreRecap(ctx);
        }
        row.className = "scanmap-row " + rowState(ctx, u, key);
      });
      catBox.appendChild(catSel);

      var collBox = document.createElement("div");
      collBox.appendChild(labelled("Collection"));
      var sel = document.createElement("select");
      var current = noLoc ? "" : (u.feature_collection ||
                                  (u.kind === "porte" ? "portes" : "terrains"));
      [["", "(aucune)"], ["portes", "portes"], ["terrains", "terrains"],
       ["hospitalites", "hospitalites"], ["tribunes", "tribunes"]]
        .forEach(function (c) {
          var o = document.createElement("option");
          o.value = c[0]; o.textContent = c[1];
          if (c[0] === current) o.selected = true;
          sel.appendChild(o);
        });
      collBox.appendChild(sel);

      var right = document.createElement("div");
      right.appendChild(labelled("Entite cartographique"));
      var input = document.createElement("input");
      input.type = "text";
      input.setAttribute("list", "scanlist-" + key.replace(/[^a-z0-9]/gi, ""));
      input.placeholder = noLoc ? "sans lieu" : "Rechercher une entite...";
      input.disabled = noLoc;
      if (u.feature_label && !noLoc) input.value = u.feature_label;
      var datalist = document.createElement("datalist");
      datalist.id = input.getAttribute("list");
      var chips = document.createElement("div");
      chips.className = "scanmap-chips";
      chips.hidden = noLoc;
      right.appendChild(input);
      right.appendChild(datalist);
      right.appendChild(chips);

      // Les suggestions du serveur couvrent le cas courant en un clic :
      // 'PORTE NORD' propose PORTE NORD PIETONS / VL / BIS.
      (u.suggestions || []).concat(u.candidates || []).forEach(function (c) {
        var chip = document.createElement("span");
        chip.className = "scanmap-chip";
        var ico = document.createElement("span");
        ico.className = "material-symbols-outlined";
        ico.textContent = COLLECTION_ICONS[c.collection] || "place";
        chip.appendChild(ico);
        chip.appendChild(document.createTextNode(c.label));
        // Le titre reste : le picto seul ne suffit pas a nommer la collection.
        chip.title = c.collection;
        chip.addEventListener("click", function () {
          setOverride(ctx, key, c._id_feature, c.collection);
          input.value = c.label;
          sel.value = c.collection;
          chips.querySelectorAll(".scanmap-chip").forEach(function (x) {
            x.classList.remove("is-set");
          });
          chip.classList.add("is-set");
          row.className = "scanmap-row " + rowState(ctx, u, key);
        });
        chips.appendChild(chip);
      });

      function loadList() {
        var coll = sel.value;
        if (!coll) { clear(datalist); return; }
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
        // Changer de collection annule le rattachement, pas la categorie :
        // ce sont deux decisions independantes.
        clearFeatureOverride(ctx, key);
        var none = sel.value === "";
        ctx.overrides[key] = ctx.overrides[key] || {};
        ctx.overrides[key].no_location = none;
        // Sans collection il n'y a rien a chercher : le champ est neutralise
        // plutot que de laisser croire qu'une saisie est attendue.
        input.disabled = none;
        input.placeholder = none ? "sans lieu" : "Rechercher une entite...";
        chips.hidden = none;
        row.dataset.resolved = (u._id_feature || none) ? "1" : "0";
        row.className = "scanmap-row " + rowState(ctx, u, key);
        loadList();
      });
      input.addEventListener("change", function () {
        var opts = datalist.querySelectorAll("option");
        for (var i = 0; i < opts.length; i++) {
          if (opts[i].value === input.value) {
            setOverride(ctx, key, opts[i].dataset.id, sel.value);
            row.className = "scanmap-row " + rowState(ctx, u, key);
            return;
          }
        }
        clearFeatureOverride(ctx, key);
        row.className = "scanmap-row " + rowState(ctx, u, key);
      });
      loadList();

      function applyIgnore() {
        var off = ignBox.checked;
        ctx.overrides[key] = ctx.overrides[key] || {};
        ctx.overrides[key].ignored = off;
        // Les controles restent lisibles mais inoperants : l'unite ne sera pas
        // ecrite, il n'y a plus rien a decider pour elle.
        [catSel, sel, input].forEach(function (c) { c.disabled = off; });
        row.className = "scanmap-row " + rowState(ctx, u, key);
        refreshIgnoreRecap(ctx);
      }
      ignBox.addEventListener("change", applyIgnore);
      if (ignBox.checked) applyIgnore();

      row.appendChild(left);
      row.appendChild(catBox);
      row.appendChild(collBox);
      row.appendChild(right);
      host.appendChild(row);
    });
  }

  // Ecarter une porte retire ses scans de la serie de l'enceinte generale,
  // qui sert de reference N-1 a tout le cockpit. Le chiffrer en direct evite
  // de le decouvrir apres coup.
  function refreshIgnoreRecap(ctx) {
    var box = $(ctx.recap);
    if (!box) return;
    var off = ctx.units.filter(function (u) {
      return isIgnored(ctx, u, unitKey(u));
    });
    if (!off.length) { box.hidden = true; return; }
    var scans = off.reduce(function (s, u) {
      return s + u.total_entree + u.total_sortie + u.total_autre;
    }, 0);
    var doors = off.filter(function (u) { return u.kind === "porte"; });
    var enceinte = doors.reduce(function (s, u) {
      return s + u.total_entree;
    }, 0);
    var txt = off.length + " unite(s) ignoree(s) — " + fmt(scans) +
      " scans exclus de l'import";
    if (doors.length) {
      txt += ", dont " + doors.length + " porte(s) : " + fmt(enceinte) +
        " entrees retirees de la serie de l'enceinte generale";
    }
    box.hidden = false;
    clear(box);
    box.appendChild(note(doors.length ? "warn" : "info",
      esc(txt) + ".<br>" + esc(off.map(function (u) { return u.name; }).join(", "))));
  }

  // Picto par collection geographique. Deux entites peuvent porter le meme
  // nom dans deux collections differentes (PORTE CIK existe deux fois) : sans
  // marqueur visible, il fallait survoler chaque pastille pour les distinguer.
  var COLLECTION_ICONS = {
    portes: "door_front",
    terrains: "landscape",
    tribunes: "stadium",
    hospitalites: "restaurant"
  };

  function labelled(text) {
    var d = document.createElement("div");
    d.className = "scanmap-col-label";
    d.textContent = text;
    return d;
  }

  // Un override porte le rattachement, la categorie, ou les deux. On fusionne
  // plutot que d'ecraser : choisir une categorie ne doit pas effacer l'entite
  // deja selectionnee.
  function setOverride(ctx, key, id, collection, category) {
    var cur = ctx.overrides[key] || {};
    if (id) { cur._id_feature = id; cur.feature_collection = collection; }
    if (category) { cur.category = category; }
    ctx.overrides[key] = cur;
  }

  function clearFeatureOverride(ctx, key) {
    var cur = ctx.overrides[key];
    if (!cur) return;
    delete cur._id_feature;
    delete cur.feature_collection;
    if (cur.category == null && cur.ignored == null) delete ctx.overrides[key];
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
      presence_reset_hour: $("#scan-reset-on").checked
        ? parseInt($("#scan-reset-hour").value, 10) : null,
      save_overrides: $("#scan-save-overrides").checked,
      overrides: {}
    };
    Object.keys(state.overrides).forEach(function (k) {
      payload.overrides[k] = {
        _id_feature: state.overrides[k]._id_feature || null,
        feature_collection: state.overrides[k].feature_collection || null,
        category: state.overrides[k].category || null,
        ignored: state.overrides[k].ignored != null
          ? state.overrides[k].ignored : null,
        no_location: state.overrides[k].no_location != null
          ? state.overrides[k].no_location : null,
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
      // Le couple qui vient d'etre importe : a la fermeture de la modale, on
      // charge le rapport correspondant plutot que de laisser l'utilisateur
      // sur celui d'avant.
      state.imported = { event: d.event, year: d.year };
      var lines = ["<strong>Import termine.</strong>",
        "Documents ecrits : " + esc(d.written.join(", ")) + "."];
      if (d.archived && d.archived.length) {
        lines.push("Archives : " + esc(d.archived.map(function (a) {
          return a.type;
        }).join(", ")) + ".");
      }
      if (d.overrides_saved) {
        lines.push(d.overrides_saved + " choix memorise(s).");
      }
      body.appendChild(note("ok", lines.join("<br>")));
      if (d.ignored && d.ignored.length) {
        // Ecarter une porte change la serie de l'enceinte, donc la reference
        // N-1 des comparaisons du cockpit : le redire apres coup.
        body.appendChild(note(
          (d.ignored_doors && d.ignored_doors.length) ? "warn" : "info",
          "<strong>" + d.ignored.length + " unite(s) ignoree(s)</strong>, " +
          "absente(s) des trois documents : " + esc(d.ignored.join(", ")) + "." +
          ((d.ignored_doors && d.ignored_doors.length)
            ? "<br>Dont " + d.ignored_doors.length + " porte(s) — la serie de " +
              "l'enceinte generale a ete recalculee sans elles."
            : "")));
      }
      if (d.unresolved && d.unresolved.length) {
        body.appendChild(note("warn",
          "Non localisees (aucune entite cartographique) : <strong>" +
          esc(d.unresolved.map(function (u) { return u.name; }).join(", ")) +
          "</strong>. Les donnees sont importees, seule la localisation manque."));
      }
      followRegen(d.regen_job, body);
    }).catch(function () {
      $("#scan-commit-progress").hidden = true;
      $("#scan-result-body").appendChild(note("err", ERRORS.reseau_indisponible));
    });
  }

  // ---- Regeneration enchainee ----------------------------------------------

  /**
   * Suit une regeneration lancee par le serveur a la suite d'un import ou
   * d'une correction de mapping, et rend son avancement dans `host`.
   *
   * Le rapport est un fichier fige : sans cet enchainement, l'utilisateur doit
   * penser a regenerer, et c'est cet oubli qui coute du temps — pas la
   * generation, qui tient sous la seconde hors appel au modele.
   */
  function followRegen(jobId, host) {
    if (!jobId) return;
    var box = note("info", "Regeneration du rapport en cours...");
    host.appendChild(box);
    var startedAt = Date.now();

    function tick() {
      apiGet("/scan-report/generate/status?job=" + encodeURIComponent(jobId))
        .then(function (d) {
          if (!d.ok) { box.innerHTML = "Regeneration : etat inconnu."; return; }
          var sec = Math.round((Date.now() - startedAt) / 1000);
          if (d.status === "running" || d.status === "queued") {
            box.innerHTML = "Regeneration du rapport — " +
              esc(d.step || "") + (sec > 2 ? " (" + sec + " s)" : "");
            setTimeout(tick, 1000);
            return;
          }
          if (d.status === "done") {
            var r = d.result || {};
            box.className = "scan-note ok";
            box.innerHTML = "<strong>Rapport regenere.</strong> " +
              esc(r.zones || 0) + " zone(s), " + esc(r.portes || 0) +
              " porte(s) — " + esc(r.size_kb || 0) + " Ko en " + sec + " s. " +
              "Recharger la page pour le voir.";
          } else {
            box.className = "scan-note err";
            box.innerHTML = "La regeneration a echoue (" +
              esc(d.error || "inconnue") + "). Relancer avec le bouton " +
              "de regeneration du bandeau.";
          }
        })
        .catch(function () {
          box.className = "scan-note err";
          box.innerHTML = "Suivi de la regeneration interrompu.";
        });
    }
    tick();
  }

  // ---- Correction du mapping apres import ----------------------------------

  function openMapping() {
    var notes = $("#scan-mapping-notes");
    clear(notes);
    $("#scan-mapping-block").hidden = true;
    $("#scan-mapping-go").hidden = true;
    $("#scan-mapping-progress").hidden = true;
    $("#scan-mapping-title").textContent =
      "Mapping des unites — " + currentEvent() + " " + currentYear();
    $("#scan-mapping-modal").hidden = false;
    mappingCtx.overrides = {};

    apiGet("/scan-report/mapping?event=" + encodeURIComponent(currentEvent()) +
           "&year=" + encodeURIComponent(currentYear()))
      .then(function (d) {
        if (!d.ok) {
          notes.appendChild(note("err", msg(d)));
          return;
        }
        mappingCtx.units = d.units || [];
        mappingCtx.categories = d.categories || [];
        mappingCtx.live = !!d.live;
        refreshMappingCount();
        // Edition live : unites lues dans l'archive du controle d'acces, aucun
        // document a reecrire. La case << memoriser >> decide seulement de la
        // portee des choix (tous les evenements ou cette edition seule).
        var saveLbl = document.querySelector("#scan-mapping-save + span");
        if (d.live) {
          notes.appendChild(note("info",
            "Source : <strong>controle d'acces live</strong> (archive HSH). " +
            "Les corrections sont memorisees dans les choix de mapping, sans " +
            "reecrire aucun document, puis le rapport est regenere."));
          if (saveLbl) saveLbl.textContent =
            "Appliquer aussi aux autres evenements et aux imports (sinon : cette edition seulement)";
        } else {
          notes.appendChild(note("info",
            "Source : <strong>" + esc(d.source_file || "import") + "</strong>" +
            (d.imported_at ? " du " + esc(d.imported_at.slice(0, 16)) : "") +
            ". Les corrections reconstruisent les trois documents depuis " +
            "<code>complet</code>, sans reprendre le classeur."));
          if (saveLbl) saveLbl.textContent = "Memoriser ces choix pour les prochains imports";
        }
        $("#scan-mapping-only-unresolved").checked = false;
        renderMapRows(mappingCtx);
        refreshIgnoreRecap(mappingCtx);
        $("#scan-mapping-block").hidden = false;
        $("#scan-mapping-go").hidden = false;
      })
      .catch(function () {
        notes.appendChild(note("err", ERRORS.reseau_indisponible));
      });
  }

  // Le compteur doit refleter l'etat en base, y compris apres application :
  // le laisser fige donnait l'impression que rien n'avait ete enregistre.
  function refreshMappingCount() {
    var u = mappingCtx.units;
    var unres = u.filter(function (x) { return !x._id_feature && !x.no_location; });
    var noloc = u.filter(function (x) { return x.no_location; });
    var off = u.filter(function (x) { return x.ignored; });
    $("#scan-mapping-count").textContent =
      u.length + " unite(s), dont " + unres.length + " a localiser, " +
      noloc.length + " sans lieu et " + off.length + " ignoree(s)";
  }

  function applyMapping() {
    var btn = $("#scan-mapping-go");
    btn.disabled = true;
    $("#scan-mapping-progress").hidden = false;

    var changes = {};
    Object.keys(mappingCtx.overrides).forEach(function (k) {
      var o = mappingCtx.overrides[k];
      changes[k] = {
        _id_feature: o._id_feature || null,
        feature_collection: o.feature_collection || null,
        category: o.category || null,
        ignored: !!o.ignored,
        no_location: !!o.no_location,
      };
    });

    apiPostJson("/scan-report/mapping", {
      event: currentEvent(), year: parseInt(currentYear(), 10),
      save_overrides: $("#scan-mapping-save").checked,
      changes: changes
    }).then(function (d) {
      $("#scan-mapping-progress").hidden = true;
      btn.disabled = false;
      var notes = $("#scan-mapping-notes");
      clear(notes);
      if (!d.ok) {
        notes.appendChild(note("err", msg(d)));
        return;
      }
      if (!d.changed) {
        notes.appendChild(note("info", "Aucun changement a appliquer."));
        return;
      }
      var lines = ["<strong>" + d.changed + " unite(s) modifiee(s).</strong>",
        d.live
          ? "Choix memorises (" + esc(d.overrides_saved || 0) + ", portee : " +
            (d.overrides_scope === "global" ? "tous les evenements" : "cette edition") +
            ") — aucun document reecrit, source controle d'acces live."
          : "Documents reecrits : " + esc((d.rewritten || []).join(", ")) +
            " — les precedents sont archives.",
        d.units + " unite(s) retenue(s)."];
      if (d.ignored && d.ignored.length) {
        lines.push("Ignorees : " + esc(d.ignored.join(", ")) + ".");
      }
      if (d.restored && d.restored.length) {
        lines.push("Reintegrees : " + esc(d.restored.join(", ")) + ".");
      }
      lines.push("Cumul d'entrees de l'enceinte : " + fmt(d.enceinte_entree) + ".");
      notes.appendChild(note("ok", lines.join("<br>")));
      followRegen(d.regen_job, notes);
      // Recharger : les etats affiches doivent refleter ce qui est en base.
      mappingCtx.overrides = {};
      apiGet("/scan-report/mapping?event=" + encodeURIComponent(currentEvent()) +
             "&year=" + encodeURIComponent(currentYear()))
        .then(function (fresh) {
          if (!fresh.ok) return;
          mappingCtx.units = fresh.units || [];
          renderMapRows(mappingCtx);
          refreshIgnoreRecap(mappingCtx);
          refreshMappingCount();
        });
    }).catch(function () {
      $("#scan-mapping-progress").hidden = true;
      btn.disabled = false;
      $("#scan-mapping-notes").appendChild(note("err", ERRORS.reseau_indisponible));
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

    var withAnalysis = $("#scan-regen-analysis").checked;
    regenStartedAt = Date.now();
    apiPostJson("/scan-report/generate", {
      event: currentEvent(), year: parseInt(currentYear(), 10),
      analysis: withAnalysis
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
        // Le compteur dit que ca avance : sans lui, l'attente sur le modele
        // (30 a 90 s) se lit comme un blocage.
        var sec = regenStartedAt
          ? Math.round((Date.now() - regenStartedAt) / 1000) : 0;
        $("#scan-regen-label").textContent =
          (d.step || d.status || "") + (sec > 2 ? " — " + sec + " s" : "");
        if (d.status === "running" || d.status === "queued") {
          setTimeout(function () { poll(jobId, fill); }, 1000);
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
          } else if (r.source === "live_controle") {
            lines.push("Source : controle d'acces live (archive HSH, " +
                       "construit en memoire, aucun document ecrit).");
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

    // Filtre d'affichage seulement : masquer une ligne ne touche pas aux
    // choix deja faits, qui vivent dans les overrides du contexte.
    [["#scan-only-unresolved", "#scan-map-rows"],
     ["#scan-mapping-only-unresolved", "#scan-mapping-rows"]].forEach(function (p) {
      var chk = $(p[0]);
      if (!chk) return;
      chk.addEventListener("change", function () {
        document.querySelectorAll(p[1] + " .scanmap-row").forEach(function (r) {
          r.hidden = chk.checked && r.dataset.resolved === "1";
        });
      });
    });

    var mapBtn = $("#scan-mapping-btn");
    if (mapBtn) mapBtn.addEventListener("click", openMapping);
    var mapGo = $("#scan-mapping-go");
    if (mapGo) mapGo.addEventListener("click", applyMapping);

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
        // Import reussi : on ouvre le couple qui vient d'etre importe. Sans
        // ca on retombe sur le rapport d'avant, qui ne montre rien de ce qui
        // vient d'etre ecrit.
        if (modal.id === "scan-import-modal" && state.imported) {
          var im = state.imported;
          state.imported = null;
          if (window.scanReportGoTo) {
            window.scanReportGoTo(im.event, im.year);
          } else {
            location.href = "/scan-report?event=" + encodeURIComponent(im.event) +
                            "&year=" + encodeURIComponent(im.year);
          }
        }
      });
    });
  });
})();
