// itineraire_admin.js - Page /admin/itineraire : administration du moteur
// d'itineraire (service valhalla-admin, relaye par valhalla_admin.py).
//
//   E1 tableau de bord : scenario charge, modifications non appliquees,
//      etat de Valhalla et du dernier chargement (GET status, toutes les 30 s)
//   E2 / E3 saisons et epreuves : creer, modifier, dupliquer, supprimer,
//      historique, editer sur la carte
//   E4 + E7 carte de travail : clic / Maj+clic sur les voies et portails,
//      bulle d'action (une ecriture pour toute la selection), portail sur un
//      point quelconque (node-at), test de trajet sur la meme carte
//   E5 chargement d'un scenario : POST load puis suivi toutes les 3 s
//   E6 historique : comparaison de deux versions, restauration (PATCH)
//
// Verrouillage optimiste : toute ecriture porte If-Match = version lue juste
// avant. Sur 409 (version obsolete), on relit, on previent ("modifie par X
// entre-temps") et on propose de reappliquer (withConflict).

(function () {
  "use strict";

  var API = "/api/admin/itineraire";
  var MODE_LABELS = window.__itModeLabels || {};
  var MODES = ["normal", "normal_piste", "god", "god_piste"];
  var MODE_COLORS = { normal: "#2563eb", normal_piste: "#0891b2", god: "#dc2626", god_piste: "#f59e0b" };
  var ACCESS_COLORS = { fermer: "#dc2626", interdire: "#f59e0b", ouvrir: "#16a34a" };
  var RULE_COLOR = "#7c3aed";       // regle sans acces (sens, vitesse, tags)
  var RACEWAY_COLOR = "#2563eb";
  var STATUS_POLL_MS = 30000;
  var LOAD_POLL_MS = 3000;
  var LAYER_NAMES = { seasons: "saison", events: "epreuve" };
  // Categories d'une regle "interdire" (champ interdire_pour, obligatoire avec
  // interdire, refuse sinon). Icones = liste blanche, seules injectees en HTML.
  var INTERDIRE_POUR = [
    ["vl", "VL", "directions_car", "Vehicules (VL)"],
    ["pietons", "pietons", "directions_walk", "Pietons"],
    ["velos", "velos", "directions_bike", "Velos"],
  ];

  var S = {
    status: null,
    seasons: [],
    events: [],
    tab: "seasons",
    statusTimer: null,
    ed: {
      map: null, layer: null, id: null, doc: null, ctxId: null, ctxDoc: null,
      groups: {}, features: {}, ruleFeatures: {}, highwaysReq: 0,
      selected: null, selLayer: null,
    },
    ts: { map: null, a: null, b: null, layer: null, results: null, hidden: {} },
    docModal: null,
    hist: null,
    loadTimer: null,
  };

  // ------------------------------------------------------------------
  // Utilitaires
  // ------------------------------------------------------------------

  function $(sel, root) { return (root || document).querySelector(sel); }
  function $all(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }

  // h("div", {className: "x", onclick: fn}, [enfants | texte])
  function h(tag, attrs, children) {
    var el = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (k) {
        var v = attrs[k];
        if (v == null || v === false) return;
        if (k === "className") el.className = v;
        else if (k === "text") el.textContent = v;
        else if (k.slice(0, 2) === "on" && typeof v === "function") el.addEventListener(k.slice(2), v);
        else if (k === "style" && typeof v === "object") Object.keys(v).forEach(function (s) { el.style[s] = v[s]; });
        else if (v === true) el.setAttribute(k, "");
        else el.setAttribute(k, v);
      });
    }
    (Array.isArray(children) ? children : (children == null ? [] : [children])).forEach(function (c) {
      if (c == null || c === false) return;
      el.appendChild(typeof c === "string" || typeof c === "number" ? document.createTextNode(String(c)) : c);
    });
    return el;
  }

  function icon(name) { return h("span", { className: "material-symbols-outlined", text: name }); }
  function clear(el) { while (el && el.firstChild) el.removeChild(el.firstChild); return el; }

  function csrf() {
    var m = document.querySelector('meta[name="csrf-token"]');
    return (m && m.content) || "";
  }

  function toast(type, msg) {
    if (typeof window.showToast === "function") window.showToast(type, msg);
    else console.log("[itineraire]", type, msg);
  }

  // Rend {status, ok, body, etag}. Ne rejette que sur panne reseau.
  function api(method, path, opts) {
    opts = opts || {};
    var url = API + path;
    if (opts.query) {
      var q = Object.keys(opts.query).filter(function (k) {
        return opts.query[k] != null && opts.query[k] !== "";
      }).map(function (k) {
        return encodeURIComponent(k) + "=" + encodeURIComponent(opts.query[k]);
      }).join("&");
      if (q) url += "?" + q;
    }
    var headers = { "Accept": "application/json" };
    if (method !== "GET") headers["X-CSRFToken"] = csrf();
    if (opts.body !== undefined) headers["Content-Type"] = "application/json";
    if (opts.ifMatch != null) headers["If-Match"] = String(opts.ifMatch);
    return fetch(url, {
      method: method, credentials: "same-origin", headers: headers,
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
    }).then(function (r) {
      var etag = r.headers.get("ETag");
      if (r.status === 204) return { status: 204, ok: true, body: null, etag: etag };
      return r.json().catch(function () { return null; }).then(function (b) {
        return { status: r.status, ok: r.ok, body: b, etag: etag };
      });
    });
  }

  // Message lisible d'une reponse en erreur (detail du service en francais,
  // erreurs de validation FastAPI, ou statut de configuration)
  function errMsg(res) {
    var b = (res && res.body) || {};
    var d = b.detail != null ? b.detail : (b.error || b.message);
    var msg = "";
    if (typeof d === "string") msg = d;
    else if (Array.isArray(d)) msg = d.map(function (x) { return (x && (x.msg || x.message)) || JSON.stringify(x); }).join(" ; ");
    else if (d && typeof d === "object") msg = d.message || d.msg || d.detail || JSON.stringify(d);
    if (res && res.status === 401) return "Cle API refusee par le service (configuration) " + (msg ? "- " + msg : "");
    if (res && res.status === 403) return "Acces refuse par le service (IP ou droits) " + (msg ? "- " + msg : "");
    if (res && res.status === 428) return "If-Match manquant (bug Cockpit)";
    return msg || ("Erreur " + (res ? res.status : "reseau"));
  }

  function conflictInfo(res) {
    var d = res && res.body && res.body.detail;
    if (d && typeof d === "object" && d.current_version != null) return d;
    return null;
  }

  function listOf(body, key) {
    if (Array.isArray(body)) return body;
    if (!body || typeof body !== "object") return [];
    var cands = [key, "items", "documents", "results", "data"];
    for (var i = 0; i < cands.length; i++) if (Array.isArray(body[cands[i]])) return body[cands[i]];
    return [];
  }

  function ruleCount(doc) {
    if (!doc) return null;
    var keys = ["rules_count", "rule_count", "n_rules", "nb_rules", "rules_total"];
    for (var i = 0; i < keys.length; i++) if (typeof doc[keys[i]] === "number") return doc[keys[i]];
    if (doc.rules && typeof doc.rules === "object") return Object.keys(doc.rules).length;
    if (typeof doc.rules === "number") return doc.rules;
    return null;
  }

  function fmtDate(iso) {
    if (!iso) return "";
    var d = new Date(iso);
    if (isNaN(d.getTime())) return String(iso);
    return d.toLocaleString("fr-FR", { timeZone: "Europe/Paris", day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
  }

  function fmtDay(s) {
    if (!s) return "";
    var m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(s));
    return m ? m[3] + "/" + m[2] + "/" + m[1] : String(s);
  }

  function fmtMin(s) {
    var m = Math.max(1, Math.round((s || 0) / 60));
    if (m < 60) return m + " min";
    return Math.floor(m / 60) + " h " + (m % 60 < 10 ? "0" : "") + (m % 60);
  }

  function activeIds() {
    var a = (S.status && S.status.active) || {};
    return { season: a.season && a.season.id, event: a.event && a.event.id };
  }

  function docName(layer, id) {
    var list = layer === "seasons" ? S.seasons : S.events;
    for (var i = 0; i < list.length; i++) if (list[i].id === id) return list[i].name || id;
    return id;
  }

  function decodePolyline6(encoded) {
    if (!encoded) return [];
    var pts = [], idx = 0, lat = 0, lon = 0, n = encoded.length;
    while (idx < n) {
      for (var axis = 0; axis < 2; axis++) {
        var shift = 0, result = 0, b;
        while (true) {
          if (idx >= n) return pts;
          b = encoded.charCodeAt(idx) - 63;
          idx++;
          result |= (b & 0x1f) << shift;
          shift += 5;
          if (b < 0x20) break;
        }
        var d = (result & 1) ? ~(result >>> 1) : (result >>> 1);
        if (axis === 0) lat += d; else lon += d;
      }
      pts.push([lat / 1e6, lon / 1e6]);
    }
    return pts;
  }

  function baseLayers(map) {
    var osm = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxNativeZoom: 19, maxZoom: 22, attribution: "&copy; OpenStreetMap",
    }).addTo(map);
    var aco = L.tileLayer("/tiles/{z}/{x}/{y}.png", { tms: true, maxZoom: 22, attribution: "ACO" });
    var ign = L.tileLayer("https://data.geopf.fr/wmts?SERVICE=WMTS&VERSION=1.0.0&REQUEST=GetTile&LAYER=ORTHOIMAGERY.ORTHOPHOTOS&STYLE=normal&TILEMATRIXSET=PM&FORMAT=image/jpeg&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}", {
      maxNativeZoom: 19, maxZoom: 22, attribution: "IGN-F/Geoplateforme",
    });
    L.control.layers({ "OSM": osm, "Satellite ACO": aco, "Satellite IGN": ign }, null, { position: "topright" }).addTo(map);
  }

  // Ecriture avec verrouillage optimiste. write(version) -> Promise<res>.
  // reread() -> Promise<doc> relit le document a jour.
  function withConflict(write, version, reread) {
    return write(version).then(function (res) {
      var c = res.status === 409 ? conflictInfo(res) : null;
      if (!c) return res;
      return reread().then(function (doc) {
        var who = (doc && doc.updated_by) || "un autre utilisateur";
        var when = doc && doc.updated_at ? " (" + fmtDate(doc.updated_at) + ")" : "";
        return window.showConfirmToast("Document modifie par " + who + when + " entre-temps (version " +
          c.current_version + "). Reappliquer votre modification sur cette version ?",
          { okLabel: "Reappliquer", cancelLabel: "Abandonner" }).then(function (ok) {
          if (!ok) return { status: 409, ok: false, body: { detail: "Modification abandonnee" }, aborted: true };
          return write((doc && doc.version) != null ? doc.version : c.current_version);
        });
      });
    });
  }

  // ------------------------------------------------------------------
  // E1 : etat
  // ------------------------------------------------------------------

  var LOAD_STATES = {
    never: ["Jamais charge", "is-off"],
    running: ["Chargement en cours", "is-run"],
    success: ["Dernier chargement reussi", "is-on"],
    rolled_back: ["Echec, scenario precedent restaure", "is-warn"],
    failed: ["Echec du chargement", "is-err"],
    interrupted: ["Chargement interrompu", "is-err"],
  };

  function loadStatus() {
    return api("GET", "/status").then(function (res) {
      if (!res.ok) {
        renderStatusError(errMsg(res));
        return null;
      }
      S.status = res.body || {};
      renderStatus();
      return S.status;
    }).catch(function () { renderStatusError("Reseau indisponible"); return null; });
  }

  function renderStatusError(msg) {
    var sc = $("#it-scenario");
    clear(sc).appendChild(h("span", { style: { color: "#b91c1c" }, text: "Service valhalla-admin indisponible" }));
    $("#it-scenario-meta").textContent = msg;
    clear($("#it-health"));
  }

  function renderStatus() {
    var st = S.status || {};
    var a = st.active || {};
    var sc = clear($("#it-scenario"));
    if (a.season) {
      sc.appendChild(document.createTextNode(a.season.name || a.season.id));
      sc.appendChild(h("span", { className: "it-ver", text: "v" + a.season.version }));
      if (a.event) {
        sc.appendChild(h("span", { className: "it-plus", text: "+" }));
        sc.appendChild(document.createTextNode(a.event.name || a.event.id));
        sc.appendChild(h("span", { className: "it-ver", text: "v" + a.event.version }));
      } else {
        sc.appendChild(h("span", { className: "it-plus", text: "(sans epreuve)" }));
      }
    } else {
      sc.textContent = "Aucun scenario charge";
    }
    var meta = [];
    if (a.loaded_at) meta.push("Charge le " + fmtDate(a.loaded_at));
    if (a.loaded_by) meta.push("par " + a.loaded_by);
    $("#it-scenario-meta").textContent = meta.join(" ");

    var hl = clear($("#it-health"));
    hl.appendChild(h("span", { className: "it-badge " + (st.valhalla_ready ? "is-on" : "is-err") },
      [icon(st.valhalla_ready ? "check_circle" : "error"), st.valhalla_ready ? "Valhalla pret" : "Valhalla indisponible"]));
    var ll = st.last_load || {};
    var ls = LOAD_STATES[ll.state] || [ll.state || "?", "is-off"];
    hl.appendChild(h("span", { className: "it-badge " + ls[1] }, [icon("history"), ls[0]]));

    var pend = st.pending_changes || [];
    var pbox = $("#it-pending");
    if (pend.length) {
      var parts = pend.map(function (p) {
        if (p === "season") return "la saison chargee";
        if (p === "event") return "l'epreuve chargee";
        return String(p);
      });
      $("#it-pending-txt").textContent = "Modifie(s) depuis le chargement : " + parts.join(" et ") + ".";
      pbox.hidden = false;
    } else {
      pbox.hidden = true;
    }
    // Badges "chargee" des listes
    renderList("seasons");
    renderList("events");
  }

  // ------------------------------------------------------------------
  // E2 / E3 : listes
  // ------------------------------------------------------------------

  function loadLists() {
    return Promise.all([api("GET", "/seasons"), api("GET", "/events")]).then(function (rs) {
      if (rs[0].ok) S.seasons = listOf(rs[0].body, "seasons");
      else toast("error", "Saisons : " + errMsg(rs[0]));
      if (rs[1].ok) S.events = listOf(rs[1].body, "events");
      else toast("error", "Epreuves : " + errMsg(rs[1]));
      renderList("seasons");
      renderList("events");
      fillDocSelects();
    });
  }

  function renderList(layer) {
    var table = $(layer === "seasons" ? "#it-seasons" : "#it-events");
    if (!table) return;
    clear(table);
    var isS = layer === "seasons";
    table.appendChild(h("thead", null, h("tr", null, [
      h("th", { text: "Nom" }),
      h("th", { text: isS ? "Annee" : "Dates" }),
      h("th", { text: "Regles" }),
      h("th", { text: "Version" }),
      h("th", { text: "Derniere modification" }),
      h("th", null),
    ])));
    var tb = h("tbody");
    var list = isS ? S.seasons : S.events;
    var act = activeIds()[isS ? "season" : "event"];
    if (!list.length) {
      tb.appendChild(h("tr", null, h("td", { className: "it-empty", colspan: "6",
        text: isS ? "Aucune saison. Creer la premiere." : "Aucune epreuve." })));
    }
    list.forEach(function (d) {
      var loaded = d.id === act;
      var n = ruleCount(d);
      var when = isS ? (d.year || "") : [fmtDay(d.starts_on), fmtDay(d.ends_on)].filter(Boolean).join(" - ");
      var mod = [fmtDate(d.updated_at), d.updated_by ? "par " + d.updated_by : ""].filter(Boolean).join(" ");
      tb.appendChild(h("tr", null, [
        h("td", null, [h("strong", { text: d.name || d.id }), " ",
          loaded ? h("span", { className: "it-badge is-on", text: "chargee" }) : null,
          h("div", { className: "it-sub it-mono", text: d.id })]),
        h("td", { text: when }),
        h("td", { text: n == null ? "" : String(n) }),
        h("td", { text: d.version != null ? "v" + d.version : "" }),
        h("td", { className: "it-sub", text: mod }),
        h("td", null, h("div", { className: "it-actions" }, [
          iconBtn("edit_road", "Editer sur la carte", function () { openEditor(layer, d.id); }),
          iconBtn("edit", "Renommer / reglages", function () { openDocModal(layer, "edit", d.id); }),
          iconBtn("content_copy", "Dupliquer", function () { openDocModal(layer, "duplicate", d.id); }),
          iconBtn("history", "Historique", function () { openHistory(layer, d.id); }),
          iconBtn("delete", loaded ? "Charge : suppression impossible" : "Supprimer",
            function () { deleteDoc(layer, d); }, loaded, true),
        ])),
      ]));
    });
    table.appendChild(tb);
  }

  function iconBtn(name, title, fn, disabled, danger) {
    return h("button", { type: "button", className: "it-icon-btn" + (danger ? " is-danger" : ""),
      title: title, "aria-label": title, disabled: !!disabled, onclick: fn }, icon(name));
  }

  function getDoc(layer, id) {
    return api("GET", "/" + layer + "/" + encodeURIComponent(id)).then(function (res) {
      if (!res.ok) throw new Error(errMsg(res));
      var doc = res.body || {};
      if (doc.version == null && res.etag) doc.version = parseInt(String(res.etag).replace(/\D/g, ""), 10);
      return doc;
    });
  }

  function deleteDoc(layer, d) {
    window.showConfirmToast("Supprimer " + LAYER_NAMES[layer] + " \"" + (d.name || d.id) + "\" ? Son historique sera perdu.",
      { okLabel: "Supprimer", cancelLabel: "Annuler" }).then(function (ok) {
      if (!ok) return;
      getDoc(layer, d.id).then(function (doc) {
        return withConflict(function (v) {
          return api("DELETE", "/" + layer + "/" + encodeURIComponent(d.id), { ifMatch: v });
        }, doc.version, function () { return getDoc(layer, d.id); });
      }).then(function (res) {
        if (res.ok) { toast("success", "Supprime"); loadLists(); }
        else if (!res.aborted) { toast("error", errMsg(res)); if (res.status === 404) loadLists(); }
      }).catch(function (e) { toast("error", e.message); });
    });
  }

  // ------------------------------------------------------------------
  // Creation / modification / duplication
  // ------------------------------------------------------------------

  function openDocModal(layer, mode, id) {
    var ov = $("#it-doc");
    var isS = layer === "seasons";
    S.docModal = { layer: layer, mode: mode, id: id || null, doc: null };
    $all("[data-for]", ov).forEach(function (el) { el.hidden = el.getAttribute("data-for") !== layer; });
    $("[data-settings]", ov).hidden = mode === "duplicate";
    $("#it-doc-name").value = "";
    $("#it-doc-year").value = "";
    $("#it-doc-start").value = "";
    $("#it-doc-end").value = "";
    $("#it-doc-desc").value = "";
    $("#it-doc-svc").value = isS && mode === "create" ? 40 : "";
    $("#it-doc-race").value = isS && mode === "create" ? 40 : "";
    $("#it-doc-svc").placeholder = isS ? "40" : "herite de la saison";
    $("#it-doc-race").placeholder = isS ? "40" : "herite de la saison";
    $("#it-doc-desc").closest("label").hidden = mode === "duplicate";
    var titles = {
      create: isS ? "Nouvelle saison" : "Nouvelle epreuve",
      edit: isS ? "Modifier la saison" : "Modifier l'epreuve",
      duplicate: isS ? "Dupliquer la saison" : "Dupliquer l'epreuve",
    };
    $("#it-doc-title").textContent = titles[mode];
    if (mode === "create") {
      if (isS) $("#it-doc-year").value = new Date().getFullYear() + 1;
      ov.hidden = false;
      $("#it-doc-name").focus();
      return;
    }
    getDoc(layer, id).then(function (doc) {
      S.docModal.doc = doc;
      var st = doc.settings || {};
      if (mode === "edit") {
        $("#it-doc-name").value = doc.name || "";
        $("#it-doc-year").value = doc.year || "";
        $("#it-doc-start").value = doc.starts_on || "";
        $("#it-doc-end").value = doc.ends_on || "";
        $("#it-doc-desc").value = doc.description || "";
        $("#it-doc-svc").value = st.service_default_maxspeed != null ? st.service_default_maxspeed : "";
        $("#it-doc-race").value = st.raceway_maxspeed != null ? st.raceway_maxspeed : "";
      } else {
        // Duplication : annee suivante proposee (Saison 2026 -> Saison 2027)
        var y = parseInt(doc.year, 10);
        var name = doc.name || "";
        if (isS && y) {
          $("#it-doc-year").value = y + 1;
          name = name.indexOf(String(y)) >= 0 ? name.replace(String(y), String(y + 1)) : name + " " + (y + 1);
        } else {
          // Epreuve (ex. Le Mans Classic a partir des 24h) : dates a ajuster
          name = name + " (copie)";
          $("#it-doc-start").value = doc.starts_on || "";
          $("#it-doc-end").value = doc.ends_on || "";
        }
        $("#it-doc-name").value = name;
      }
      ov.hidden = false;
      $("#it-doc-name").focus();
    }).catch(function (e) { toast("error", e.message); });
  }

  function intOrNull(v) {
    if (v === "" || v == null) return null;
    var n = parseInt(v, 10);
    return isNaN(n) ? null : n;
  }

  function saveDocModal() {
    var m = S.docModal;
    if (!m) return;
    var isS = m.layer === "seasons";
    var name = $("#it-doc-name").value.trim();
    if (!name) { toast("warning", "Nom obligatoire"); return; }
    var body = { name: name };
    if (isS) {
      var y = intOrNull($("#it-doc-year").value);
      if (!y) { toast("warning", "Annee obligatoire"); return; }
      body.year = y;
    } else {
      body.starts_on = $("#it-doc-start").value || null;
      body.ends_on = $("#it-doc-end").value || null;
    }
    if (m.mode !== "duplicate") {
      body.description = $("#it-doc-desc").value.trim();
      var svc = intOrNull($("#it-doc-svc").value);
      var race = intOrNull($("#it-doc-race").value);
      if (isS && (svc == null || race == null)) { toast("warning", "Les deux vitesses sont obligatoires pour une saison"); return; }
      body.settings = { service_default_maxspeed: svc, raceway_maxspeed: race };
    }
    var btn = $("#it-doc-save");
    btn.disabled = true;
    var p;
    if (m.mode === "create") {
      p = api("POST", "/" + m.layer, { body: body });
    } else if (m.mode === "duplicate") {
      p = api("POST", "/" + m.layer + "/" + encodeURIComponent(m.id) + "/duplicate", { body: body });
    } else {
      p = withConflict(function (v) {
        return api("PATCH", "/" + m.layer + "/" + encodeURIComponent(m.id), { body: body, ifMatch: v });
      }, m.doc && m.doc.version, function () { return getDoc(m.layer, m.id); });
    }
    p.then(function (res) {
      btn.disabled = false;
      if (!res.ok) { if (!res.aborted) toast("error", errMsg(res)); return; }
      $("#it-doc").hidden = true;
      toast("success", m.mode === "create" ? "Cree" : m.mode === "duplicate" ? "Copie creee" : "Enregistre");
      loadLists();
      loadStatus();
      if (S.m.map && m.id === mDocId()) reloadEditorDoc();
    }).catch(function () { btn.disabled = false; toast("error", "Reseau indisponible"); });
  }

  // ------------------------------------------------------------------
  // E5 : chargement d'un scenario
  // ------------------------------------------------------------------

  function openLoad() {
    var ov = $("#it-load");
    var act = activeIds();
    var ss = clear($("#it-load-season"));
    S.seasons.forEach(function (d) {
      ss.appendChild(h("option", { value: d.id, text: (d.name || d.id) + (d.version != null ? " (v" + d.version + ")" : ""),
        selected: d.id === act.season }));
    });
    var es = clear($("#it-load-event"));
    es.appendChild(h("option", { value: "", text: "Aucune epreuve" }));
    S.events.forEach(function (d) {
      es.appendChild(h("option", { value: d.id, text: (d.name || d.id) + (d.version != null ? " (v" + d.version + ")" : ""),
        selected: d.id === act.event }));
    });
    $("#it-load-confirm").checked = false;
    $("#it-load-go").disabled = true;
    $("#it-load-go").hidden = false;
    $("#it-load-form").hidden = false;
    $("#it-load-progress").hidden = true;
    ov.hidden = false;
    // Un chargement deja en cours : afficher directement son suivi
    if (S.status && S.status.last_load && S.status.last_load.state === "running") showLoadProgress();
  }

  function startLoad() {
    var season = $("#it-load-season").value;
    if (!season) { toast("warning", "Choisir une saison"); return; }
    if (!$("#it-load-confirm").checked) return;
    var ev = $("#it-load-event").value || null;
    $("#it-load-go").disabled = true;
    api("POST", "/load", { body: { season_id: season, event_id: ev } }).then(function (res) {
      if (res.status === 409) {
        toast("warning", "Un chargement est deja en cours : affichage de son suivi");
        showLoadProgress();
        return;
      }
      if (!res.ok) { $("#it-load-go").disabled = false; toast("error", errMsg(res)); return; }
      showLoadProgress(res.body);
    }).catch(function () { $("#it-load-go").disabled = false; toast("error", "Reseau indisponible"); });
  }

  function showLoadProgress(initial) {
    $("#it-load-form").hidden = true;
    $("#it-load-go").hidden = true;
    $("#it-load-progress").hidden = false;
    clear($("#it-load-result"));
    if (initial && initial.last_load) renderLoad(initial.last_load);
    else if (initial && initial.state) renderLoad(initial);
    pollLoad();
  }

  function pollLoad() {
    if (S.loadTimer) clearTimeout(S.loadTimer);
    api("GET", "/status").then(function (res) {
      if (res.ok) {
        S.status = res.body || {};
        renderStatus();
        var ll = S.status.last_load || {};
        renderLoad(ll);
        if (ll.state === "running") { S.loadTimer = setTimeout(pollLoad, LOAD_POLL_MS); return; }
        loadLists();
        return;
      }
      // Service redemarre pendant le chargement : on insiste
      S.loadTimer = setTimeout(pollLoad, LOAD_POLL_MS);
    }).catch(function () { S.loadTimer = setTimeout(pollLoad, LOAD_POLL_MS); });
  }

  function renderLoad(ll) {
    var st = clear($("#it-load-state"));
    var ls = LOAD_STATES[ll.state] || [ll.state || "?", "is-off"];
    if (ll.state === "running") st.appendChild(h("span", { className: "it-spin" }));
    st.appendChild(h("span", { className: "it-badge " + ls[1], text: ls[0] }));
    var ol = clear($("#it-load-steps"));
    (ll.steps || []).forEach(function (s) {
      var t = s.at ? fmtDate(s.at).split(" ").pop() : "";
      ol.appendChild(h("li", null, [t ? h("span", { text: t }) : null, s.msg || s.message || JSON.stringify(s)]));
    });
    ol.scrollTop = ol.scrollHeight;
    var out = clear($("#it-load-result"));
    if (ll.state === "success") {
      out.appendChild(h("div", { className: "it-alert is-ok" }, [icon("check_circle"),
        h("div", { text: "Scenario charge. Trajets de controle :" })]));
      out.appendChild(referenceTable(ll.reference_routes || {}));
      if (ll.patch) {
        var p = ll.patch;
        out.appendChild(h("p", { className: "it-hint", text:
          (p.rules_applied != null ? p.rules_applied + " regle(s) appliquee(s), " : "") +
          (p.raceway_ways != null ? p.raceway_ways + " troncons de piste, " : "") +
          (p.service_speed_patched != null ? p.service_speed_patched + " voies de service a vitesse par defaut" : "") }));
      }
    } else if (ll.state === "rolled_back") {
      out.appendChild(h("div", { className: "it-alert is-warn" }, [icon("undo"),
        h("div", null, [h("strong", { text: "Echec du chargement : " }), String(ll.error || "erreur inconnue"),
          h("br"), "Le scenario precedent est toujours actif."])]));
    } else if (ll.state === "failed" || ll.state === "interrupted") {
      out.appendChild(h("div", { className: "it-alert is-error" }, [icon("error"),
        h("div", null, [h("strong", { text: ll.state === "failed" ? "Echec du chargement : " : "Chargement interrompu : " }),
          String(ll.error || "erreur inconnue"), h("br"),
          "Contacter l'administrateur systeme du serveur Valhalla."])]));
    }
  }

  function referenceTable(refs) {
    var t = h("table", { className: "it-table" });
    t.appendChild(h("thead", null, h("tr", null, [h("th", { text: "Mode" }), h("th", { text: "Distance" }),
      h("th", { text: "Temps" }), h("th", { text: "Piste" })])));
    var tb = h("tbody");
    var keys = MODES.filter(function (m) { return refs[m]; });
    Object.keys(refs).forEach(function (k) { if (keys.indexOf(k) < 0) keys.push(k); });
    keys.forEach(function (k) {
      var r = refs[k] || {};
      tb.appendChild(h("tr", null, [
        h("td", { text: MODE_LABELS[k] || k }),
        h("td", { text: r.length_km != null ? Number(r.length_km).toFixed(2) + " km" : (r.error || "-") }),
        h("td", { text: r.time_s != null ? fmtMin(r.time_s) : "-" }),
        h("td", { text: r.uses_raceway ? "oui" : "non" }),
      ]));
    });
    if (!keys.length) tb.appendChild(h("tr", null, h("td", { colspan: "4", className: "it-empty", text: "Aucun trajet de controle" })));
    t.appendChild(tb);
    return t;
  }

  // ------------------------------------------------------------------
  // E6 : historique
  // ------------------------------------------------------------------

  function openHistory(layer, id) {
    S.hist = { layer: layer, id: id, versions: [], cache: {} };
    $("#it-hist-title").textContent = "Historique : " + docName(layer, id);
    clear($("#it-hist-diff")).appendChild(h("p", { className: "it-hint", text: "Chargement..." }));
    $("#it-hist").hidden = false;
    api("GET", "/" + layer + "/" + encodeURIComponent(id) + "/history").then(function (res) {
      if (!res.ok) { toast("error", errMsg(res)); return; }
      var vs = ((res.body && res.body.versions) || []).slice().sort(function (a, b) { return b - a; });
      S.hist.versions = vs;
      var sa = clear($("#it-hist-a")), sb = clear($("#it-hist-b"));
      vs.forEach(function (v, i) {
        sa.appendChild(h("option", { value: v, text: "v" + v + (i === 0 ? " (actuelle)" : ""), selected: i === 1 || vs.length === 1 }));
        sb.appendChild(h("option", { value: v, text: "v" + v + (i === 0 ? " (actuelle)" : ""), selected: i === 0 }));
      });
      renderHistDiff();
    });
  }

  function histVersion(v) {
    var H = S.hist;
    if (H.cache[v]) return Promise.resolve(H.cache[v]);
    return api("GET", "/" + H.layer + "/" + encodeURIComponent(H.id) + "/history/" + v).then(function (res) {
      if (!res.ok) throw new Error(errMsg(res));
      H.cache[v] = res.body || {};
      return H.cache[v];
    });
  }

  function renderHistDiff() {
    var H = S.hist;
    if (!H || !H.versions.length) return;
    var va = parseInt($("#it-hist-a").value, 10), vb = parseInt($("#it-hist-b").value, 10);
    $("#it-hist-restore").disabled = va === H.versions[0];
    Promise.all([histVersion(va), histVersion(vb)]).then(function (docs) {
      var A = docs[0], B = docs[1];
      var box = clear($("#it-hist-diff"));
      box.appendChild(h("p", { className: "it-hint", text:
        "v" + va + " : " + fmtDate(A.updated_at) + (A.updated_by ? " par " + A.updated_by : "") + "   |   " +
        "v" + vb + " : " + fmtDate(B.updated_at) + (B.updated_by ? " par " + B.updated_by : "") }));
      var ra = A.rules || {}, rb = B.rules || {};
      var keys = Object.keys(ra).concat(Object.keys(rb).filter(function (k) { return !(k in ra); })).sort();
      var rows = [];
      keys.forEach(function (k) {
        var a = ra[k], b = rb[k];
        if (a && !b) rows.push(diffRow(k, "it-diff-del", a, null, "absente en v" + vb));
        else if (!a && b) rows.push(diffRow(k, "it-diff-add", null, b, "ajoutee en v" + vb));
        else if (JSON.stringify(a) !== JSON.stringify(b)) rows.push(diffRow(k, "it-diff-mod", a, b, "modifiee"));
      });
      var sa = JSON.stringify(A.settings || {}), sb = JSON.stringify(B.settings || {});
      var sec = h("div", { className: "it-diff-sec" });
      sec.appendChild(h("h5", { text: "Regles : v" + va + " -> v" + vb + " (" + rows.length + " difference(s), " +
        Object.keys(rb).length + " regle(s) en v" + vb + ")" }));
      if (!rows.length) sec.appendChild(h("p", { className: "it-hint", text: "Regles identiques." }));
      rows.forEach(function (r) { sec.appendChild(r); });
      box.appendChild(sec);
      var meta = [];
      if (sa !== sb) meta.push(diffRow("reglages", "it-diff-mod", A.settings || {}, B.settings || {}, "modifies"));
      if ((A.name || "") !== (B.name || "")) meta.push(diffRow("nom", "it-diff-mod", A.name, B.name, "modifie"));
      if (meta.length) {
        var s2 = h("div", { className: "it-diff-sec" }, h("h5", { text: "Document" }));
        meta.forEach(function (r) { s2.appendChild(r); });
        box.appendChild(s2);
      }
    }).catch(function (e) { toast("error", e.message); });
  }

  function diffRow(key, cls, a, b, label) {
    return h("div", { className: "it-diff-row" }, [
      h("div", null, [h("strong", { className: "it-mono", text: key }), h("div", { className: cls, text: label })]),
      h("div", { className: "it-mono", text: a == null ? "-" : (typeof a === "string" ? a : JSON.stringify(a)) }),
      h("div", { className: "it-mono", text: b == null ? "-" : (typeof b === "string" ? b : JSON.stringify(b)) }),
    ]);
  }

  function restoreVersion() {
    var H = S.hist;
    var v = parseInt($("#it-hist-a").value, 10);
    window.showConfirmToast("Restaurer les regles et reglages de la v" + v + " ? Une nouvelle version sera creee.",
      { okLabel: "Restaurer", cancelLabel: "Annuler" }).then(function (ok) {
      if (!ok) return;
      Promise.all([histVersion(v), getDoc(H.layer, H.id)]).then(function (r) {
        var old = r[0], cur = r[1];
        var body = { rules: old.rules || {}, settings: old.settings || {} };
        return withConflict(function (ver) {
          return api("PATCH", "/" + H.layer + "/" + encodeURIComponent(H.id), { body: body, ifMatch: ver });
        }, cur.version, function () { return getDoc(H.layer, H.id); });
      }).then(function (res) {
        if (!res.ok) { if (!res.aborted) toast("error", errMsg(res)); return; }
        toast("success", "Version v" + v + " restauree");
        loadLists();
        loadStatus();
        if (S.m.map && H.id === mDocId()) reloadEditorDoc();
        openHistory(H.layer, H.id);
      }).catch(function (e) { toast("error", e.message); });
    });
  }

  // ------------------------------------------------------------------
  // Carte de travail (E4 + E7 sur une seule carte)
  // ------------------------------------------------------------------
  //
  // Tout se fait a la souris, sans identifiant OSM :
  //   - voies de la vue chargees au fil des deplacements (cache par id),
  //     barrieres affichees en zoomant, piste toujours ;
  //   - clic = selection d'un objet, Maj+clic = ajout / retrait (plusieurs
  //     troncons d'une meme route) ; la bulle applique la meme action a
  //     toute la selection en UNE ecriture (PATCH rules, une version) ;
  //   - "devient piste" : sens de course choisi une fois pour la selection,
  //     deduit troncon par troncon en suivant leur enchainement (les
  //     troncons OSM d'une route ne sont pas tous traces dans le meme sens) ;
  //   - "Tester un trajet" : A -> B dans les 4 modes sur la meme carte.

  var CIRCUIT_CENTER = [47.937, 0.214];
  var ARROWS_MIN_ZOOM = 15;
  var WAYS_MIN_ZOOM = 13;
  var BARRIERS_MIN_ZOOM = 15;
  var MINOR_WAYS = { footway: 1, path: 1, cycleway: 1, steps: 1, pedestrian: 1, bridleway: 1, corridor: 1 };

  S.m = {
    map: null, groups: null, feats: {}, lyrs: {}, barriers: [], reqId: 0, moveTimer: null,
    seasonId: null, eventId: null, doc: null, seasonDoc: null,
    sel: [], flip: false, test: false, ta: null, tb: null, tres: null, thidden: {},
  };

  function mLayer() { return S.m.eventId ? "events" : "seasons"; }
  function mDocId() { return S.m.eventId || S.m.seasonId; }
  function mQuery() { return { season: S.m.seasonId || "", event: S.m.eventId || "none" }; }

  // Compatibilite avec les listes / modales (Editer sur la carte, historique)
  function openEditor(layer, id) {
    if (layer === "events") {
      S.m.eventId = id;
      if (!S.m.seasonId) S.m.seasonId = activeIds().season || (S.seasons[0] && S.seasons[0].id) || null;
    } else {
      S.m.seasonId = id;
      S.m.eventId = null;
    }
    if (S.tab !== "carte") switchTab("carte");
    fillDocSelects();
    reloadEditorDoc();
  }

  function editorDefault() {
    if (S.m.seasonId) return;
    S.m.seasonId = activeIds().season || (S.seasons[0] && S.seasons[0].id) || null;
    S.m.eventId = activeIds().event || null;
    fillDocSelects();
    reloadEditorDoc();
  }

  function fillDocSelects() {
    var ss = clear($("#it-m-season"));
    S.seasons.forEach(function (d) { ss.appendChild(h("option", { value: d.id, text: d.name || d.id })); });
    if (S.m.seasonId) ss.value = S.m.seasonId;
    var es = clear($("#it-m-event"));
    es.appendChild(h("option", { value: "", text: "Aucune (modifier la saison)" }));
    S.events.forEach(function (d) { es.appendChild(h("option", { value: d.id, text: d.name || d.id })); });
    es.value = S.m.eventId || "";
  }

  function renderEditing() {
    var b = clear($("#it-m-editing"));
    var d = S.m.doc;
    if (!d) return;
    var isEv = mLayer() === "events";
    b.appendChild(h("span", { className: "it-badge " + (isEv ? "is-warn" : "is-run") },
      [icon("edit"), "Vous modifiez " + (isEv ? "l'epreuve " : "la saison ") + (d.name || d.id) + " (v" + d.version + ")"]));
    var n = Object.keys(d.rules || {}).length;
    b.appendChild(h("span", { className: "it-sub", text: n + " regle(s)" }));
  }

  function reloadEditorDoc() {
    var M = S.m;
    if (!M.seasonId) return Promise.resolve();
    var jobs = [getDoc(mLayer(), mDocId())];
    if (M.eventId) jobs.push(getDoc("seasons", M.seasonId));
    return Promise.all(jobs).then(function (r) {
      M.doc = r[0];
      M.seasonDoc = M.eventId ? r[1] : r[0];
      renderEditing();
      reloadFeatures();
    }).catch(function (e) { toast("error", e.message); });
  }

  // ---------------- chargement et rendu ----------------

  function initMap() {
    var M = S.m;
    if (M.map) { setTimeout(function () { M.map.invalidateSize(); }, 50); return; }
    M.map = L.map("it-m-map", { zoomControl: true, preferCanvas: true, renderer: L.canvas({ tolerance: 6 }) });
    // Portails sur leur propre calque, AU-DESSUS des voies : sur un calque
    // commun, les voies chargees ensuite passaient dessus et prenaient le
    // clic d'un portail pose sur une voie (ex. C11 2, node/2951025110)
    // Rendu SVG et non canvas : un canvas couvre toute la carte et avalait
    // les clics destines aux voies du dessous (seuls les portails restaient
    // cliquables) ; en SVG, seuls les cercles captent le clic.
    M.map.createPane("itBarriers").style.zIndex = 450;
    M.barrierRenderer = L.svg({ pane: "itBarriers" });
    baseLayers(M.map);
    M.map.setView(CIRCUIT_CENTER, 14);
    M.groups = {
      ways: L.layerGroup().addTo(M.map),
      barriers: L.layerGroup(),
      // Portails portant une regle : visibles a tous les zooms
      ruled: L.layerGroup().addTo(M.map),
      overlay: L.layerGroup().addTo(M.map),
      sel: L.layerGroup().addTo(M.map),
      test: L.layerGroup().addTo(M.map),
    };
    M.map.on("moveend", function () {
      if (M.moveTimer) clearTimeout(M.moveTimer);
      M.moveTimer = setTimeout(loadView, 250);
      syncBarriersVisibility();
      redrawOverlay();
    });
    M.map.on("click", function (e) { onMapClick(e.latlng); });
    setTimeout(function () { M.map.invalidateSize(); }, 50);
  }

  function reloadFeatures() {
    var M = S.m;
    if (!M.map) return;
    M.feats = {};
    M.lyrs = {};
    M.groups.ways.clearLayers();
    M.groups.barriers.clearLayers();
    M.groups.ruled.clearLayers();
    M.barriers = [];
    var q = mQuery();
    api("GET", "/features", { query: Object.assign({ kind: "raceways" }, q) }).then(function (res) {
      if (res.ok) addFeatures((res.body && res.body.features) || []);
    });
    api("GET", "/features", { query: Object.assign({ kind: "barriers" }, q) }).then(function (res) {
      if (!res.ok) { toast("error", "Barrieres : " + errMsg(res)); return; }
      M.barriers = (res.body && res.body.features) || [];
      M.barriers.forEach(function (f) { M.feats[f.id] = f; drawFeature(f); });
      redrawOverlay();
      syncBarriersVisibility();
    });
    // Objets portant une regle : visibles meme hors de la vue chargee
    ["highways", "barriers"].forEach(function (kind) {
      api("GET", "/features", { query: Object.assign({ kind: kind, only_rules: "true" }, q) }).then(function (res) {
        if (res.ok) addFeatures((res.body && res.body.features) || []);
        renderRuleList();
      });
    });
    loadView();
  }

  function loadView() {
    var M = S.m;
    if (!M.map || !M.seasonId) return;
    var hint = $("#it-m-zoomhint");
    if (M.map.getZoom() < WAYS_MIN_ZOOM) { hint.hidden = false; return; }
    hint.hidden = true;
    var b = M.map.getBounds().pad(0.2);
    var bbox = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()].map(function (x) { return x.toFixed(5); }).join(",");
    var id = ++M.reqId;
    api("GET", "/features", { query: Object.assign({ kind: "highways", bbox: bbox }, mQuery()) }).then(function (res) {
      if (id !== M.reqId) return;
      if (!res.ok) { toast("error", "Voies : " + errMsg(res)); return; }
      addFeatures((res.body && res.body.features) || []);
    });
  }

  function addFeatures(list) {
    var M = S.m;
    list.forEach(function (f) {
      M.feats[f.id] = f;
      if ((f.geometry || {}).type === "Point" && M.barriers.indexOf(f) < 0) {
        // barriere avec regle hors liste : on l'ajoute
        if (!M.barriers.some(function (x) { return x.id === f.id; })) M.barriers.push(f);
      }
      drawFeature(f);
    });
    redrawOverlay();
  }

  function waysVisible(f) {
    var p = f.properties || {};
    if (!$("#it-m-minor").checked && MINOR_WAYS[(p.osm_tags || {}).highway] && !hasRule(p.effective_rule)) return false;
    return true;
  }

  function drawFeature(f) {
    var M = S.m;
    var old = M.lyrs[f.id];
    if (old) { M.groups.ways.removeLayer(old); M.groups.barriers.removeLayer(old); M.groups.ruled.removeLayer(old); }
    var g = f.geometry || {};
    var st = featureStyle(f);
    var lyr = null;
    if (g.type === "LineString") {
      if (!waysVisible(f)) { delete M.lyrs[f.id]; return; }
      var ll = (g.coordinates || []).map(function (c) { return [c[1], c[0]]; });
      lyr = L.polyline(ll, { color: st.color, weight: st.weight, opacity: st.opacity, dashArray: st.dashArray });
      lyr.addTo(M.groups.ways);
    } else if (g.type === "Point") {
      lyr = L.circleMarker([g.coordinates[1], g.coordinates[0]], {
        pane: "itBarriers", renderer: M.barrierRenderer,
        radius: hasRule((f.properties || {}).effective_rule) ? 8 : 6, color: "#ffffff", weight: 2,
        fillColor: st.color === "#475569" ? "#334155" : st.color, fillOpacity: 1, dashArray: st.dashArray,
      });
      lyr.addTo(hasRule((f.properties || {}).effective_rule) ? M.groups.ruled : M.groups.barriers);
    }
    if (!lyr) return;
    var name = ((f.properties || {}).osm_tags || {}).name;
    // Texte OSM tiers : jamais en HTML (Leaflet injecte les chaines en innerHTML)
    lyr.bindTooltip(h("span", { text: name || featureKind(f) }), { sticky: true });
    lyr.on("click", function (e) {
      L.DomEvent.stopPropagation(e);
      if (S.m.test) { onMapClick(e.latlng); return; }
      selectIds([f.id], e.originalEvent && (e.originalEvent.shiftKey || e.originalEvent.ctrlKey));
    });
    M.lyrs[f.id] = lyr;
  }

  function syncBarriersVisibility() {
    var M = S.m;
    var show = $("#it-m-barriers").checked && M.map.getZoom() >= BARRIERS_MIN_ZOOM;
    if (show && !M.map.hasLayer(M.groups.barriers)) M.groups.barriers.addTo(M.map);
    if (!show && M.map.hasLayer(M.groups.barriers)) M.map.removeLayer(M.groups.barriers);
  }

  function featureKind(f) {
    var p = f.properties || {};
    var t = p.osm_tags || {};
    if ((f.geometry || {}).type === "Point") {
      if (p.on_way) return "Point de " + p.on_way;
      return t.barrier ? "Barriere (" + t.barrier + ")" : "Point";
    }
    if (isPiste(p)) return "Piste";
    return "Voie" + (t.highway ? " (" + t.highway + ")" : "");
  }

  // Statut piste effectif : effective_rule.piste s'il est present (regle
  // "devient piste" / "n'est plus piste"), sinon la piste OSM (raceway)
  function isPiste(p) {
    var eff = (p && p.effective_rule) || {};
    return eff.piste != null ? !!eff.piste : !!(p && p.raceway);
  }

  function hasRule(r) { return !!(r && Object.keys(r).some(function (k) { return k !== "comment" && r[k] != null; })); }

  function pourLabels(list) {
    return (list || []).map(function (v) {
      for (var i = 0; i < INTERDIRE_POUR.length; i++) if (INTERDIRE_POUR[i][0] === v) return INTERDIRE_POUR[i][1];
      return String(v);
    }).join(", ");
  }

  function ruleSummary(r) {
    if (!r) return "";
    var parts = [];
    if (r.access === "interdire" && r.interdire_pour && r.interdire_pour.length) {
      parts.push("interdire (" + pourLabels(r.interdire_pour) + ")");
    } else if (r.access) parts.push(r.access);
    if (r.piste === true) parts.push("devient piste");
    else if (r.piste === false) parts.push("n'est plus piste");
    // Le sens exact se lit sur la carte (fleches) ; "du trace" ne parle a personne
    if (r.oneway) parts.push(r.oneway === "no" ? "double sens" : "sens unique");
    if (r.maxspeed) parts.push(r.maxspeed + " km/h");
    if (r.tags && Object.keys(r.tags).length) parts.push(Object.keys(r.tags).length + " tag(s)");
    return parts.join(", ");
  }

  function featureStyle(f) {
    var p = f.properties || {};
    var eff = p.effective_rule || {};
    var fromEvent = mLayer() === "events" && hasRule(p.event_rule);
    var minor = MINOR_WAYS[(p.osm_tags || {}).highway];
    var color, weight = minor ? 2 : 3, opacity = minor ? 0.45 : 0.6, dash = null;
    if (eff.access && ACCESS_COLORS[eff.access]) { color = ACCESS_COLORS[eff.access]; weight = 6; opacity = 0.95; }
    else if (isPiste(p)) { color = RACEWAY_COLOR; weight = 5; opacity = 0.85; }
    else if (hasRule(eff)) { color = RULE_COLOR; weight = 5; opacity = 0.9; }
    else if (p.osm_restricted) { color = "#6b7280"; weight = minor ? 2 : 4; opacity = 0.8; dash = "3 5"; }
    else { color = minor ? "#94a3b8" : "#475569"; }
    if (fromEvent) dash = "10 7";
    return { color: color, weight: weight, opacity: opacity, dashArray: dash };
  }

  // Fleches de sens (piste, regles de sens) et pictogrammes des interdictions
  function redrawOverlay() {
    var M = S.m;
    M.groups.overlay.clearLayers();
    Object.keys(M.feats).forEach(function (id) {
      var f = M.feats[id];
      var p = f.properties || {};
      var g = f.geometry || {};
      var c = g.coordinates || [];
      var eff = p.effective_rule || {};
      if (g.type === "LineString" && c.length >= 2) {
        var piste = isPiste(p);
        var ow = eff.oneway || (piste ? ((p.osm_tags || {}).oneway || "yes") : null);
        if ((piste || eff.oneway) && ow && ow !== "no" && M.map.getZoom() >= ARROWS_MIN_ZOOM) {
          var i = Math.max(0, Math.floor(c.length / 2) - 1);
          arrowMarker(c[i], c[i + 1], ow === "-1", "#1e3a8a", 16).addTo(M.groups.overlay);
        }
      }
      if (eff.access === "interdire" && Array.isArray(eff.interdire_pour)) {
        var icons = INTERDIRE_POUR.filter(function (x) { return eff.interdire_pour.indexOf(x[0]) >= 0; })
          .map(function (x) { return x[2]; });
        var at = g.type === "Point" ? [c[1], c[0]] : (c.length ? [c[Math.floor(c.length / 2)][1], c[Math.floor(c.length / 2)][0]] : null);
        if (at && icons.length) {
          var w = 8 + icons.length * 17;
          // Noms d'icones issus de la liste blanche INTERDIRE_POUR uniquement
          L.marker(at, { interactive: false, icon: L.divIcon({
            className: "",
            html: "<div class='it-picto'>" + icons.map(function (n) {
              return "<span class='material-symbols-outlined'>" + n + "</span>";
            }).join("") + "</div>",
            iconSize: [w, 22], iconAnchor: [w / 2, g.type === "Point" ? 30 : 11],
          }) }).addTo(M.groups.overlay);
        }
      }
    });
  }

  // Fleche entre deux points [lon, lat] ; reverse = sens inverse du trace
  function arrowMarker(a, b, reverse, color, size) {
    if (reverse) { var t = a; a = b; b = t; }
    var ang = Math.atan2((b[0] - a[0]) * Math.cos(a[1] * Math.PI / 180), b[1] - a[1]) * 180 / Math.PI;
    // Couleur et taille : constantes du code, jamais de donnee tierce
    return L.marker([(a[1] + b[1]) / 2, (a[0] + b[0]) / 2], { interactive: false, icon: L.divIcon({
      className: "",
      html: "<span class='material-symbols-outlined it-arrow' style='font-size:" + size + "px;color:" + color +
        ";transform:rotate(" + (ang - 90).toFixed(0) + "deg);'>arrow_forward</span>",
      iconSize: [size, size], iconAnchor: [size / 2, size / 2],
    }) });
  }

  // ---------------- selection ----------------

  function selectIds(ids, add) {
    var M = S.m;
    if (M.test) return;
    if (add) {
      ids.forEach(function (id) {
        var i = M.sel.indexOf(id);
        if (i >= 0) M.sel.splice(i, 1); else M.sel.push(id);
      });
    } else {
      M.sel = ids.slice();
      M.flip = false;
    }
    renderSelection();
    renderBubble();
  }

  function clearSelection() {
    S.m.sel = [];
    renderSelection();
    renderBubble();
  }

  function selWays() {
    return S.m.sel.filter(function (id) { return id.indexOf("way/") === 0; });
  }

  // Sens de course de chaque troncon par rapport a son trace (+1 = trace,
  // -1 = inverse), en suivant l'enchainement des troncons a partir du
  // premier selectionne ; troncons isoles : orientation du plus proche.
  function courseOrientation() {
    var M = S.m;
    var ways = selWays().filter(function (id) { return M.feats[id] && (M.feats[id].geometry || {}).type === "LineString"; });
    var out = {};
    if (!ways.length) return out;
    function key(c) { return c[0].toFixed(6) + "," + c[1].toFixed(6); }
    function ends(id) { var c = M.feats[id].geometry.coordinates; return { f: key(c[0]), l: key(c[c.length - 1]), c: c }; }
    out[ways[0]] = M.flip ? -1 : 1;
    var changed = true;
    while (changed) {
      changed = false;
      ways.forEach(function (b) {
        if (out[b]) return;
        var eb = ends(b);
        for (var i = 0; i < ways.length; i++) {
          var a = ways[i];
          if (!out[a]) continue;
          var ea = ends(a);
          var exit = out[a] === 1 ? ea.l : ea.f, entry = out[a] === 1 ? ea.f : ea.l;
          var o = eb.f === exit ? 1 : eb.l === exit ? -1 : eb.l === entry ? 1 : eb.f === entry ? -1 : 0;
          if (o) { out[b] = o; changed = true; return; }
        }
      });
      if (!changed) {
        // Troncon isole : meme orientation que le troncon oriente le plus proche
        var rest = ways.filter(function (w) { return !out[w]; });
        if (!rest.length) break;
        var b = rest[0], cb = ends(b).c;
        var vb = [cb[cb.length - 1][0] - cb[0][0], cb[cb.length - 1][1] - cb[0][1]];
        var best = null, bd = Infinity;
        ways.forEach(function (a) {
          if (!out[a]) return;
          var ca = ends(a).c, ma = ca[Math.floor(ca.length / 2)], mb = cb[Math.floor(cb.length / 2)];
          var d = Math.pow(ma[0] - mb[0], 2) + Math.pow(ma[1] - mb[1], 2);
          if (d < bd) { bd = d; best = a; }
        });
        var ca2 = ends(best).c;
        var va = [(ca2[ca2.length - 1][0] - ca2[0][0]) * out[best], (ca2[ca2.length - 1][1] - ca2[0][1]) * out[best]];
        out[b] = (va[0] * vb[0] + va[1] * vb[1]) >= 0 ? 1 : -1;
        changed = true;
      }
    }
    return out;
  }

  function renderSelection() {
    var M = S.m;
    M.groups.sel.clearLayers();
    // Fleches bleues = sens choisi (sens de course d'une piste ou sens unique)
    var pisteMode = directionMode();
    var orient = pisteMode ? courseOrientation() : null;
    M.sel.forEach(function (id) {
      var f = M.feats[id];
      if (!f || !f.geometry) return;
      var g = f.geometry, c = g.coordinates;
      if (g.type === "LineString") {
        L.polyline(c.map(function (x) { return [x[1], x[0]]; }), { color: "#facc15", weight: 13, opacity: 0.55, interactive: false }).addTo(M.groups.sel);
        // Fleches : sens de course (bleu) si "devient piste", sinon sens du trace (jaune fonce)
        var n = Math.min(3, c.length - 1);
        for (var k = 0; k < n; k++) {
          var i = Math.min(c.length - 2, Math.floor((k + 0.5) * (c.length - 1) / n));
          var rev = orient ? orient[id] === -1 : false;
          arrowMarker(c[i], c[i + 1], rev, orient ? "#1d4ed8" : "#a16207", 22).addTo(M.groups.sel);
        }
      } else if (g.type === "Point") {
        L.circleMarker([c[1], c[0]], { radius: 14, color: "#facc15", weight: 4, fill: false, interactive: false }).addTo(M.groups.sel);
      }
    });
    // Une seule voie : ses points deviennent cliquables pour y poser un
    // portail (identifiant du noeud trouve par Cockpit, cf. node-at)
    if (M.sel.length === 1 && !pisteMode) {
      var f1 = M.feats[M.sel[0]];
      if (f1 && f1.geometry && f1.geometry.type === "LineString") {
        f1.geometry.coordinates.forEach(function (c) {
          var dot = L.circleMarker([c[1], c[0]], { radius: 5, color: "#0f172a", weight: 2, fillColor: "#ffffff", fillOpacity: 1 });
          dot.bindTooltip("Poser un portail ici");
          dot.on("click", function (e) { L.DomEvent.stopPropagation(e); pickNode(f1, c); });
          dot.addTo(M.groups.sel);
        });
      }
    }
  }

  // Point d'une voie -> noeud OSM -> selection du point (bulle portail)
  function pickNode(way, c) {
    var M = S.m;
    var wayId = (way.properties || {}).osm_id || parseInt(String(way.id).split("/")[1], 10);
    api("GET", "/node-at", { query: { way: wayId, lon: c[0], lat: c[1] } }).then(function (res) {
      if (!res.ok) { toast("warning", errMsg(res)); return; }
      var ref = res.body.node;
      if (!M.feats[ref]) {
        // Croisement : le meme point appartient a une autre voie chargee
        var k = c[0].toFixed(7) + "," + c[1].toFixed(7);
        var junction = Object.keys(M.feats).some(function (id) {
          var f = M.feats[id];
          if (id === way.id || !f.geometry || f.geometry.type !== "LineString") return false;
          return f.geometry.coordinates.some(function (x) { return x[0].toFixed(7) + "," + x[1].toFixed(7) === k; });
        });
        M.feats[ref] = { id: ref, type: "Feature", geometry: { type: "Point", coordinates: [c[0], c[1]] },
          properties: { osm_type: "node", osm_id: res.body.osm_id, osm_tags: {}, raceway: false,
            on_way: ((way.properties || {}).osm_tags || {}).name || way.id, junction: junction } };
      }
      selectIds([ref], false);
    }).catch(function () { toast("error", "Reseau indisponible"); });
  }

  // ---------------- bulle d'action ----------------

  var BUBBLE = { access: "", piste: "", oneway: "" };

  function currentChoice(name) { return BUBBLE[name] || ""; }

  // Un sens est a choisir sur la carte : "devient piste" ou "sens unique"
  function directionMode() {
    return BUBBLE.piste === "true" || BUBBLE.oneway === "unique" || !!(S.wz && S.wz.arrows);
  }

  function chips(name, options, onChange) {
    var wrap = h("div", { className: "it-chips" });
    options.forEach(function (o) {
      var b = h("button", { type: "button", className: "it-chip" + (BUBBLE[name] === o[0] ? " is-on" : "") +
        (o[2] ? " " + o[2] : ""), text: o[1] });
      b.addEventListener("click", function () {
        BUBBLE[name] = o[0];
        $all(".it-chip", wrap).forEach(function (x) { x.classList.remove("is-on"); });
        b.classList.add("is-on");
        if (onChange) onChange();
      });
      wrap.appendChild(b);
    });
    return wrap;
  }

  function ruleText(r) {
    if (!r || (!hasRule(r) && !r.comment)) return "aucune";
    return (ruleSummary(r) || "-") + (r.comment ? " - " + r.comment : "");
  }

  function renderBubble() {
    var M = S.m;
    var box = $("#it-m-bubble");
    clear(box);
    if (M.test) { renderTestPanel(box); box.hidden = false; return; }
    if (!M.sel.length || !M.doc) { box.hidden = true; return; }
    box.hidden = false;
    var feats = M.sel.map(function (id) { return M.feats[id]; });
    var single = M.sel.length === 1 ? M.sel[0] : null;
    var f0 = feats[0];
    var p0 = (f0 && f0.properties) || {};
    var own = single ? ((M.doc.rules || {})[single] || null) : null;
    var allWays = selWays().length === M.sel.length;
    BUBBLE = { access: "", piste: "", oneway: "" };
    if (own) {
      BUBBLE.access = own.access || "";
      BUBBLE.piste = own.piste === true ? "true" : own.piste === false ? "false" : "";
    }

    // En-tete
    var names = [];
    feats.forEach(function (f) {
      var n = f && ((f.properties || {}).osm_tags || {}).name;
      if (n && names.indexOf(n) < 0) names.push(n);
    });
    var title = single ? (names[0] || (f0 ? featureKind(f0) : single)) : M.sel.length + " objets selectionnes";
    box.appendChild(h("div", { className: "it-b-head" }, [
      icon(single && f0 && f0.geometry && f0.geometry.type === "Point" ? "fence" : (isPiste(p0) ? "sports_motorsports" : "route")),
      h("strong", { text: title }),
      iconBtn("close", "Fermer", clearSelection),
    ]));
    box.appendChild(h("div", { className: "it-sub" }, single
      ? [f0 ? featureKind(f0) : "", "  -  ", h("a", { href: "https://www.openstreetmap.org/" + single, target: "_blank", rel: "noopener", className: "it-mono", text: single })]
      : [names.length ? names.slice(0, 3).join(", ") + (names.length > 3 ? "..." : "") : "", " ", h("span", { className: "it-hint", text: "(Maj+clic pour ajouter ou retirer un troncon)" })]));

    if (single && p0.on_way && !own) {
      box.appendChild(h("div", { className: "it-hint", text: "Nouveau portail : Interdire ou Fermer transforme ce point en portail." }));
    }
    if (single && p0.junction) {
      box.appendChild(h("div", { className: "it-hint it-b-warn", text: "Point de croisement : le fermer coupe aussi la voie qui croise (et la piste). " +
        "Pour empecher seulement la traversee, fermer plutot les troncons d'acces de part et d'autre." }));
    }
    if (single && f0 && (f0.geometry || {}).type === "LineString") {
      box.appendChild(h("div", { className: "it-hint", text: "Pour poser un portail sur cette voie : cliquer l'un de ses points blancs sur la carte." }));
    }

    // Regles en place
    if (single) {
      var sr = mLayer() === "events" ? ((M.seasonDoc && M.seasonDoc.rules || {})[single] || p0.season_rule) : own;
      var lines = [h("div", null, [h("span", { className: "it-k", text: "Saison : " }), ruleText(sr)])];
      if (mLayer() === "events") lines.push(h("div", null, [h("span", { className: "it-k", text: "Epreuve : " }), ruleText(own)]));
      box.appendChild(h("div", { className: "it-b-rules" }, lines));
    } else {
      var withRule = M.sel.filter(function (id) { return (M.doc.rules || {})[id]; }).length;
      if (withRule) box.appendChild(h("div", { className: "it-hint", text: withRule + " objet(s) ont deja une regle : seuls les champs choisis ci-dessous changent." }));
    }

    // Acces
    var ipour = h("div", { className: "it-b-sec it-ipour" });
    var accessSec = h("div", { className: "it-b-sec" }, [
      h("div", { className: "it-k", text: "Acces" }),
      chips("access", [["", "inchange"], ["ouvrir", "Ouvrir", "c-open"], ["interdire", "Interdire", "c-warn"],
        ["fermer", "Fermer", "c-close"], ["retirer", "Retirer l'acces"]], function () { ipour.hidden = BUBBLE.access !== "interdire"; }),
    ]);
    box.appendChild(accessSec);
    // Interdire pour : aucune case pre-cochee sur une nouvelle interdiction
    var boxes = h("div", { className: "it-seg" });
    INTERDIRE_POUR.forEach(function (c) {
      var cb = h("input", { type: "checkbox", name: "it-b-ipour", value: c[0] });
      cb.checked = !!(own && own.access === "interdire" && (own.interdire_pour || []).indexOf(c[0]) >= 0);
      var lab = h("label", { className: cb.checked ? "is-checked" : "" }, [cb, icon(c[2]), c[3]]);
      cb.addEventListener("change", function () { lab.classList.toggle("is-checked", cb.checked); });
      boxes.appendChild(lab);
    });
    ipour.appendChild(h("span", { className: "it-hint", text: "Interdire pour (au moins une case)" }));
    ipour.appendChild(boxes);
    if (single && mLayer() === "events") {
      var srr = (M.seasonDoc && M.seasonDoc.rules || {})[single];
      if (srr && srr.access === "interdire" && (srr.interdire_pour || []).length) {
        ipour.appendChild(h("div", { className: "it-hint it-b-warn", text: "La saison interdit deja pour " + pourLabels(srr.interdire_pour) +
          " : la liste de l'epreuve la remplace (pas de cumul), recocher pour garder." }));
      }
    }
    ipour.hidden = BUBBLE.access !== "interdire";
    box.appendChild(ipour);
    box.appendChild(h("div", { className: "it-hint", text: "Ouvrir : tout le monde. Interdire : les categories cochees (les vehicules d'intervention passent). Fermer : tout le monde, intervention comprise." }));

    // Piste et sens de circulation (voies seulement). Le sens se choisit une
    // fois pour toute la selection (fleches bleues + Inverser), Cockpit en
    // deduit oneway "yes" / "-1" troncon par troncon (courseOrientation).
    if (allWays) {
      if (single && own && (own.oneway === "yes" || own.oneway === "-1")) {
        BUBBLE.oneway = "unique";
        M.flip = own.oneway === "-1";
      } else if (single && own && own.oneway === "no") {
        BUBBLE.oneway = "no";
      }
      var dirHint = h("div", { className: "it-hint" });
      var dirBox = h("div", { className: "it-b-sec it-b-course" }, [
        dirHint,
        h("button", { type: "button", className: "btn btn-secondary", onclick: function () { M.flip = !M.flip; renderSelection(); } },
          [icon("swap_horiz"), "Inverser le sens"]),
      ]);
      var syncDir = function () {
        dirBox.hidden = !directionMode();
        dirHint.textContent = BUBBLE.piste === "true"
          ? "Sens de course : celui des fleches bleues sur la carte."
          : "Seul le sens des fleches bleues sera autorise : l'autre sens devient un sens interdit.";
        renderSelection();
      };
      box.appendChild(h("div", { className: "it-b-sec" }, [
        h("div", { className: "it-k", text: "Piste" }),
        chips("piste", [["", "inchange"], ["true", "Devient piste", "c-piste"], ["false", "N'est plus piste"], ["osm", "Suivre OSM"]], syncDir),
      ]));
      box.appendChild(h("div", { className: "it-b-sec" }, [
        h("div", { className: "it-k", text: "Sens de circulation" }),
        chips("oneway", [["", "inchange"], ["unique", "Sens unique", "c-sens"], ["no", "Double sens"], ["osm", "Suivre OSM"]], syncDir),
        h("span", { className: "it-hint", text: "Respecte par les vehicules et les velos, ignore en intervention (god). Pietons non concernes." }),
      ]));
      box.appendChild(dirBox);
      syncDir();
    }

    // Motif
    box.appendChild(h("label", { className: "it-b-field" }, ["Motif",
      h("input", { id: "it-b-comment", className: "form-input", maxlength: "300", value: (own && own.comment) || "",
        placeholder: single ? "ex. Portail condamne" : "vide = inchange" })]));

    // Plus d'options
    var more = h("details", { className: "it-expert" }, [h("summary", { text: "Plus d'options (vitesse, tags)" })]);
    if (allWays) {
      more.appendChild(h("label", { className: "it-b-field" }, ["Vitesse km/h (vide = inchange, 0 = retirer)",
        h("input", { id: "it-b-speed", type: "number", min: "0", max: "130", className: "form-input",
          value: own && own.maxspeed != null ? own.maxspeed : "" })]));
    }
    if (single) {
      var ta = h("textarea", { id: "it-b-tags", rows: "3", className: "form-input", placeholder: "{\"motor_vehicle\": \"no\"}" });
      if (own && own.tags) ta.value = JSON.stringify(own.tags);
      more.appendChild(h("label", { className: "it-b-field" }, ["Tags OSM forces (expert, null supprime)", ta]));
      var tags = p0.osm_tags || {};
      var tg = h("div", { className: "it-tags" });
      Object.keys(tags).sort().forEach(function (k) { tg.appendChild(h("span", { className: "k", text: k })); tg.appendChild(h("span", { className: "v", text: String(tags[k]) })); });
      more.appendChild(h("div", { className: "it-b-sec" }, [h("span", { className: "it-hint", text: "Tags OSM d'origine" }), tg]));
    }
    box.appendChild(more);

    var wzBlock = wazeBubbleBlock();
    if (wzBlock) box.appendChild(wzBlock);

    var anyRule = M.sel.some(function (id) { return (M.doc.rules || {})[id]; });
    box.appendChild(h("div", { className: "it-ed-btns" }, [
      h("button", { type: "button", className: "btn btn-primary", onclick: applyBubble }, [icon("check"), "Appliquer"]),
      anyRule ? h("button", { type: "button", className: "btn btn-secondary", style: { color: "#dc2626" }, onclick: deleteSelRules },
        [icon("delete"), single ? "Supprimer la regle" : "Supprimer les regles"]) : null,
    ]));
    box.appendChild(h("div", { className: "it-hint", text: "Effet sur les itineraires au prochain chargement du scenario." }));
  }

  // Regles resultantes pour la selection, a partir des regles du document
  function buildRules(baseRules) {
    var M = S.m;
    var rules = JSON.parse(JSON.stringify(baseRules || {}));
    var single = M.sel.length === 1;
    var acc = BUBBLE.access, pv = BUBBLE.piste, ow = BUBBLE.oneway;
    var pour = $all("input[name='it-b-ipour']:checked").map(function (cb) { return cb.value; });
    if (acc === "interdire" && !pour.length) throw new Error("Interdire : cocher au moins une categorie (VL, pietons, velos)");
    var comment = ($("#it-b-comment") || {}).value;
    comment = comment == null ? "" : comment.trim();
    var speedEl = $("#it-b-speed");
    var speed = speedEl && speedEl.value !== "" ? parseInt(speedEl.value, 10) : null;
    if (speed != null && speed !== 0 && (isNaN(speed) || speed < 5 || speed > 130)) throw new Error("Vitesse entre 5 et 130 km/h (0 = retirer)");
    var tagsRaw = single && $("#it-b-tags") ? $("#it-b-tags").value.trim() : null;
    var tags = null;
    if (tagsRaw) {
      try { tags = JSON.parse(tagsRaw); } catch (e) { throw new Error("Tags : JSON invalide"); }
      if (!tags || typeof tags !== "object" || Array.isArray(tags)) throw new Error("Tags : objet {cle: valeur} attendu");
    }
    var orient = directionMode() ? courseOrientation() : {};
    M.sel.forEach(function (id) {
      var r = rules[id] ? JSON.parse(JSON.stringify(rules[id])) : {};
      var isWay = id.indexOf("way/") === 0;
      if (acc === "ouvrir" || acc === "fermer") { r.access = acc; delete r.interdire_pour; }
      else if (acc === "interdire") { r.access = acc; r.interdire_pour = pour.slice(); }
      else if (acc === "retirer") { delete r.access; delete r.interdire_pour; }
      if (isWay) {
        if (pv === "true") {
          r.piste = true;
          // Route qui devient piste : sens de course unique obligatoire
          if (!((M.feats[id] || {}).properties || {}).raceway) r.oneway = orient[id] === -1 ? "-1" : "yes";
        } else if (pv === "false") r.piste = false;
        else if (pv === "osm") delete r.piste;
        // Sens : "unique" = sens des fleches, deduit pour chaque troncon
        // (une route qui devient piste a deja recu son sens de course)
        var forced = pv === "true" && !((M.feats[id] || {}).properties || {}).raceway;
        if (!forced) {
          if (ow === "unique") r.oneway = orient[id] === -1 ? "-1" : "yes";
          else if (ow === "no") r.oneway = "no";
          else if (ow === "osm") delete r.oneway;
        }
        if (speed === 0) delete r.maxspeed;
        else if (speed != null) r.maxspeed = speed;
      }
      if (single) {
        if (comment) r.comment = comment; else delete r.comment;
        if (tagsRaw !== null) { if (tags && Object.keys(tags).length) r.tags = tags; else delete r.tags; }
      } else if (comment) r.comment = comment;
      if (r.access == null && r.piste == null && r.oneway == null && r.maxspeed == null && !r.tags) delete rules[id];
      else rules[id] = r;
    });
    return rules;
  }

  function patchRules(make, okMsg) {
    var M = S.m;
    var layer = mLayer(), id = mDocId();
    var rules;
    try { rules = make(M.doc.rules); } catch (e) { toast("warning", e.message); return; }
    if (JSON.stringify(rules) === JSON.stringify(M.doc.rules || {})) { toast("info", "Aucun changement"); return; }
    withConflict(function (v) {
      // Sur conflit, les regles sont recalculees sur le document relu
      var body = { rules: v === M.doc.version ? rules : make(M.doc.rules) };
      return api("PATCH", "/" + layer + "/" + encodeURIComponent(id), { body: body, ifMatch: v });
    }, M.doc.version, function () {
      return getDoc(layer, id).then(function (d) { M.doc = d; return d; });
    }).then(function (res) {
      if (!res.ok) {
        if (!res.aborted) toast("error", errMsg(res));
        if (res.status === 404) reloadEditorDoc();
        return;
      }
      M.doc = res.body && res.body.rules ? res.body : M.doc;
      toast("success", okMsg + " (effet au prochain chargement du scenario)");
      var keep = M.sel.slice();
      reloadEditorDoc().then(function () { M.sel = keep; renderSelection(); renderBubble(); });
      loadStatus();
      loadLists();
    }).catch(function (e) { toast("error", (e && e.message) || "Reseau indisponible"); });
  }

  function applyBubble() {
    var n = S.m.sel.length;
    patchRules(buildRules, n > 1 ? n + " objets mis a jour" : "Regle enregistree");
  }

  function deleteSelRules() {
    var M = S.m;
    var n = M.sel.filter(function (id) { return (M.doc.rules || {})[id]; }).length;
    window.showConfirmToast("Supprimer " + (n > 1 ? "les " + n + " regles" : "la regle") + " de la selection ?",
      { okLabel: "Supprimer", cancelLabel: "Annuler" }).then(function (ok) {
      if (!ok) return;
      patchRules(function (base) {
        var r = JSON.parse(JSON.stringify(base || {}));
        M.sel.forEach(function (id) { delete r[id]; });
        return r;
      }, n > 1 ? n + " regles supprimees" : "Regle supprimee");
    });
  }

  // ---------------- liste des regles ----------------

  function renderRuleList() {
    var M = S.m;
    var box = clear($("#it-m-rules"));
    if (!M.doc) return;
    var rules = M.doc.rules || {};
    var keys = Object.keys(rules).sort();
    $("#it-m-rules-count").textContent = String(keys.length);
    if (!keys.length) box.appendChild(h("div", { className: "it-hint", text: "Aucune regle : cliquer une voie ou un portail sur la carte." }));
    keys.forEach(function (k) {
      var r = rules[k];
      var f = M.feats[k];
      var name = f && ((f.properties || {}).osm_tags || {}).name;
      box.appendChild(h("div", { className: "it-ed-rule", onclick: function () { focusIds([k]); } }, [
        h("span", { className: "it-dot", style: { background: ACCESS_COLORS[r.access] || (r.piste ? RACEWAY_COLOR : RULE_COLOR) } }),
        h("span", { className: "it-ed-rule-txt" }, [name || (f ? featureKind(f) : k), " : ", ruleSummary(r), r.comment ? " (" + r.comment + ")" : ""]),
      ]));
    });
  }

  function focusIds(ids) {
    var M = S.m;
    var pts = [];
    ids.forEach(function (id) {
      var g = (M.feats[id] || {}).geometry;
      if (!g) return;
      if (g.type === "Point") pts.push([g.coordinates[1], g.coordinates[0]]);
      else (g.coordinates || []).forEach(function (c) { pts.push([c[1], c[0]]); });
    });
    if (pts.length) M.map.fitBounds(pts, { maxZoom: 18, padding: [60, 60] });
    selectIds(ids, false);
  }

  // ---------------- recherche ----------------

  var _searchTimer = null;
  function onSearchInput() {
    var q = $("#it-m-q").value.trim();
    if (_searchTimer) clearTimeout(_searchTimer);
    var res = $("#it-m-results");
    if (q.length < 2) { res.hidden = true; return; }
    _searchTimer = setTimeout(function () {
      api("GET", "/search", { query: { q: q } }).then(function (r) {
        clear(res);
        if (!r.ok) { res.appendChild(h("div", { className: "it-ed-res", text: errMsg(r) })); res.hidden = false; return; }
        var items = (r.body && r.body.items) || [];
        // Une rue = plusieurs troncons : une ligne par nom, qui selectionne tout
        var groups = {}, order = [];
        items.forEach(function (it) {
          var k = it.name ? it.name + "|" + it.type : it.id;
          if (!groups[k]) { groups[k] = []; order.push(k); }
          groups[k].push(it);
        });
        if (!order.length) res.appendChild(h("div", { className: "it-ed-res", text: "Aucun resultat" }));
        order.forEach(function (k) {
          var g = groups[k];
          res.appendChild(h("div", { className: "it-ed-res", onclick: function () { res.hidden = true; gotoItems(g); } }, [
            h("strong", { text: g[0].name || g[0].id }), h("span", { text: g[0].type }),
            h("span", { className: "it-mono", text: g.length > 1 ? g.length + " troncons" : g[0].id })]));
        });
        res.hidden = false;
      });
    }, 300);
  }

  function gotoItems(items) {
    var M = S.m;
    var pts = [];
    items.forEach(function (it) {
      if (it.bounds) { pts.push(it.bounds[0]); pts.push(it.bounds[1]); } else if (it.center) pts.push(it.center);
    });
    if (pts.length) M.map.fitBounds(pts, { maxZoom: 18, padding: [60, 60] });
    var ids = items.map(function (it) { return it.id; });
    var tries = 0;
    (function waitSel() {
      if (ids.every(function (id) { return M.feats[id]; }) || ++tries > 20) { selectIds(ids.filter(function (id) { return M.feats[id]; }), false); return; }
      setTimeout(waitSel, 300);
    })();
  }

  // ---------------- test de trajet (E7) ----------------

  function toggleTest(on) {
    var M = S.m;
    M.test = on;
    M.ta = M.tb = null;
    M.tres = null;
    M.groups.test.clearLayers();
    $("#it-m-test").classList.toggle("is-on", on);
    $("#it-m-map").classList.toggle("is-testing", on);
    if (on) { M.sel = []; renderSelection(); }
    renderBubble();
  }

  function onMapClick(latlng) {
    var M = S.m;
    if (!M.test) { if (M.sel.length) clearSelection(); return; }
    var ll = [latlng.lat, latlng.lng];
    if (!M.ta || M.tb) { M.ta = ll; M.tb = null; M.tres = null; }
    else M.tb = ll;
    drawTest();
    renderBubble();
    if (M.ta && M.tb) {
      api("POST", "/route-test", { body: { from: M.ta, to: M.tb } }).then(function (res) {
        if (!res.ok || !res.body || !res.body.ok) { toast("error", errMsg(res)); return; }
        M.tres = res.body.results || [];
        drawTest(true);
        renderBubble();
      }).catch(function () { toast("error", "Reseau indisponible"); });
    }
  }

  function drawTest(fit) {
    var M = S.m;
    M.groups.test.clearLayers();
    var bounds = [];
    (M.tres || []).slice().reverse().forEach(function (r) {
      if (!r.ok || M.thidden[r.mode]) return;
      var pts = decodePolyline6(r.polyline);
      if (pts.length < 2) return;
      L.polyline(pts, { color: MODE_COLORS[r.mode], weight: r.mode.indexOf("god") === 0 ? 7 : 5, opacity: 0.85,
        dashArray: r.mode.slice(-6) === "_piste" ? "12 8" : null, interactive: false }).addTo(M.groups.test);
      bounds = bounds.concat(pts);
    });
    if (M.ta) L.circleMarker(M.ta, { radius: 8, fillColor: "#16a34a", color: "#fff", weight: 3, fillOpacity: 1, interactive: false }).addTo(M.groups.test);
    if (M.tb) L.circleMarker(M.tb, { radius: 8, fillColor: "#dc2626", color: "#fff", weight: 3, fillOpacity: 1, interactive: false }).addTo(M.groups.test);
    if (fit && bounds.length) M.map.fitBounds(bounds, { padding: [40, 40], maxZoom: 17 });
  }

  function renderTestPanel(box) {
    var M = S.m;
    box.appendChild(h("div", { className: "it-b-head" }, [icon("science"), h("strong", { text: "Tester un trajet" }),
      iconBtn("close", "Quitter le test", function () { toggleTest(false); })]));
    box.appendChild(h("div", { className: "it-hint", text: !M.ta ? "Cliquer le depart sur la carte." : !M.tb ? "Cliquer l'arrivee." :
      M.tres ? "Cliquer la carte pour un nouveau depart." : "Calcul dans les quatre modes..." }));
    box.appendChild(h("div", { className: "it-hint it-b-warn", text: "Calcul sur le scenario CHARGE : les modifications non chargees n'y sont pas encore." }));
    if (!M.tres) return;
    var t = h("table", { className: "it-table it-ts-table" });
    var tb = h("tbody");
    MODES.forEach(function (m) {
      var r = M.tres.filter(function (x) { return x.mode === m; })[0];
      var cb = h("input", { type: "checkbox", title: "Afficher" });
      cb.checked = !M.thidden[m];
      cb.addEventListener("change", function () { M.thidden[m] = !cb.checked; drawTest(); });
      tb.appendChild(h("tr", null, [
        h("td", null, cb),
        h("td", null, [h("span", { className: "it-swatch", style: { background: MODE_COLORS[m] } }), MODE_LABELS[m] || m]),
        h("td", { text: !r ? "-" : r.ok ? ((r.distance_m || 0) / 1000).toFixed(2) + " km" : (r.error === "no_route" ? "aucun chemin" : "indisponible") }),
        h("td", { text: r && r.ok ? fmtMin(r.duration_s) : "-" }),
        h("td", { text: r && r.ok && r.uses_raceway ? "piste" : "" }),
      ]));
    });
    t.appendChild(tb);
    box.appendChild(t);
  }

  // ------------------------------------------------------------------
  // Waze : fermetures publiees vers les conducteurs (waze_feed.py)
  // ------------------------------------------------------------------
  //
  // Une fermeture Waze reprend des troncons dont la regle FERME la voie
  // (fermer, interdire aux VL, devient piste) dans le document edite. Waze
  // relit le flux toutes les quelques minutes ; les horaires sont saisis
  // ici (heure de Paris). Independant du scenario charge.

  var WZ_STATUS = {
    brouillon: ["Brouillon", "is-off"],
    programmee: ["Programmee", "is-run"],
    en_cours: ["En cours", "is-err"],
    terminee: ["Terminee", "is-off"],
    desynchronisee: ["Desynchronisee", "is-warn"],
  };

  S.wz = { closures: [], cfg: null, arrows: false, edit: null, keys: [], pts: null };

  function wzRuleCloses(r) {
    if (!r) return false;
    if (r.access === "fermer") return true;
    if (r.access === "interdire" && (r.interdire_pour || []).indexOf("vl") >= 0) return true;
    return r.piste === true;
  }

  function loadWaze() {
    return Promise.all([api("GET", "/waze/closures"), api("GET", "/waze/config")]).then(function (rs) {
      if (rs[0].ok) S.wz.closures = (rs[0].body && rs[0].body.items) || [];
      if (rs[1].ok) S.wz.cfg = rs[1].body;
      renderWazeTab();
      if (S.tab === "carte" && S.m.sel.length) renderBubble();
    });
  }

  function wzClosuresFor(keys) {
    return S.wz.closures.filter(function (c) {
      return c.layer === mLayer() && c.doc_id === mDocId() && c.rule_keys.some(function (k) { return keys.indexOf(k) >= 0; });
    });
  }

  // Bloc "Waze" de la bulle (selection de voies)
  function wazeBubbleBlock() {
    var M = S.m;
    var ways = selWays();
    if (!ways.length || ways.length !== M.sel.length) return null;
    var rules = M.doc.rules || {};
    var ok = ways.filter(function (id) { return wzRuleCloses(rules[id]); });
    var existing = wzClosuresFor(ways);
    var sec = h("div", { className: "it-b-sec it-b-waze" }, [h("div", { className: "it-k" }, [icon("campaign"), " Waze"])]);
    existing.forEach(function (c) {
      var st = WZ_STATUS[c.status] || [c.status, "is-off"];
      sec.appendChild(h("div", { className: "it-wz-line" }, [
        h("span", { className: "it-badge " + st[1], text: st[0] }),
        h("span", { text: c.street + " - " + c.slots.length + " plage(s)" }),
        h("button", { type: "button", className: "btn btn-secondary btn-sm", onclick: function () { openWazeDialog(c); }, text: "Modifier" }),
      ]));
    });
    if (ok.length === ways.length) {
      sec.appendChild(h("button", { type: "button", className: "btn btn-secondary", onclick: function () { openWazeDialog(null); } },
        [icon("campaign"), "Publier sur Waze..."]));
    } else if (!existing.length) {
      sec.appendChild(h("div", { className: "it-hint", text: "Pour publier sur Waze : d'abord Fermer, Interdire aux VL ou Devient piste, puis Appliquer." }));
    }
    return sec;
  }

  // Troncons enchaines -> polyline [lat, lon] dans le sens des fleches
  function chainPolyline(ids) {
    var M = S.m;
    var orient = courseOrientation();
    function key(c) { return c[0].toFixed(6) + "," + c[1].toFixed(6); }
    var info = ids.map(function (id) {
      var c = M.feats[id].geometry.coordinates.slice();
      if (orient[id] === -1) c.reverse();
      return { id: id, c: c, a: key(c[0]), b: key(c[c.length - 1]) };
    });
    var heads = info.filter(function (x) { return !info.some(function (y) { return y !== x && y.b === x.a; }); });
    if (heads.length !== 1) return { error: "Selectionner des troncons qui se suivent, sans trou ni embranchement (un debut et une fin)." };
    var order = [heads[0]], seen = {};
    seen[heads[0].id] = true;
    while (order.length < info.length) {
      var last = order[order.length - 1];
      var next = info.filter(function (y) { return !seen[y.id] && y.a === last.b; })[0];
      if (!next) return { error: "Selectionner des troncons qui se suivent, sans trou ni embranchement (un debut et une fin)." };
      seen[next.id] = true;
      order.push(next);
    }
    var pts = [];
    order.forEach(function (x) {
      x.c.forEach(function (p, i) { if (i === 0 && pts.length) return; pts.push([p[1], p[0]]); });
    });
    return { keys: order.map(function (x) { return x.id; }), pts: pts };
  }

  function wzSlotRow(s) {
    var tr = h("tr");
    var locked = !!(s && s.started);
    var st = h("input", { type: "datetime-local", className: "form-input", value: (s && s.start) || "", disabled: locked || null });
    var en = h("input", { type: "datetime-local", className: "form-input", value: (s && s.end) || "", disabled: (s && s.ended) || null });
    tr.dataset.sid = (s && s.sid) || "";
    tr.appendChild(h("td", null, [h("span", { className: "it-hint", text: "Debut" }), st]));
    tr.appendChild(h("td", null, [h("span", { className: "it-hint", text: "Fin" }), en]));
    tr.appendChild(h("td", null, locked ? h("span", { className: "it-hint", text: s.ended ? "terminee" : "en cours" })
      : iconBtn("delete", "Retirer la plage", function () { tr.parentNode.removeChild(tr); }, false, true)));
    return tr;
  }

  function wzDefaultSlot() {
    var d = S.m.doc || {};
    if (d.starts_on) return { start: d.starts_on + "T00:00", end: (d.ends_on || d.starts_on) + "T23:59" };
    var now = new Date(Date.now() + 3600000);
    function f(dt) {
      var p = function (n) { return (n < 10 ? "0" : "") + n; };
      return dt.getFullYear() + "-" + p(dt.getMonth() + 1) + "-" + p(dt.getDate()) + "T" + p(dt.getHours()) + ":00";
    }
    return { start: f(now), end: f(new Date(now.getTime() + 6 * 3600000)) };
  }

  function openWazeDialog(existing) {
    var M = S.m, W = S.wz;
    W.edit = existing;
    W.arrows = false;
    var ov = $("#it-waze");
    if (existing) {
      W.keys = existing.rule_keys;
      W.pts = existing.polyline;
      $("#it-wz-title").textContent = "Fermeture Waze : " + existing.street;
    } else {
      var ch = chainPolyline(selWays());
      if (ch.error) { toast("warning", ch.error); return; }
      W.keys = ch.keys;
      W.pts = ch.pts;
      $("#it-wz-title").textContent = "Publier sur Waze";
    }
    var names = {};
    W.keys.forEach(function (id) {
      var t = ((M.feats[id] || {}).properties || {}).osm_tags || {};
      var n = t.name || t.ref;
      if (n) names[n] = (names[n] || 0) + 1;
    });
    var best = Object.keys(names).sort(function (a, b) { return names[b] - names[a]; })[0] || "";
    $("#it-wz-summary").textContent = W.keys.length + " troncon(s) de " + (mLayer() === "events" ? "l'epreuve " : "la saison ") +
      ((M.doc && M.doc.name) || mDocId()) + (Object.keys(names).length > 1 ? " - attention, plusieurs noms de rue : Waze n'accepte qu'une rue par fermeture" : "");
    $("#it-wz-street").value = existing ? existing.street : best;
    $("#it-wz-desc").value = existing ? existing.description : "";
    $("#it-wz-desc-count").textContent = $("#it-wz-desc").value.length + " / 40";
    var dir = existing ? existing.direction : "BOTH_DIRECTIONS";
    $all("input[name='it-wz-dir']").forEach(function (r) { r.checked = r.value === dir; r.disabled = !!existing; });
    $("#it-wz-flip").hidden = true;
    var res = clear($("#it-wz-check-res"));
    if (existing && existing.street_check) res.textContent = existing.street_check.match ? "Nom verifie dans Waze." : "Nom non trouve dans Waze lors de la derniere verification.";
    var tb = clear($("#it-wz-slots"));
    (existing ? existing.slots : [wzDefaultSlot()]).forEach(function (s) { tb.appendChild(wzSlotRow(s)); });
    $("#it-wz-publish").textContent = existing && existing.publish ? "Enregistrer (publiee)" : "Publier";
    $("#it-wz-draft").hidden = !!(existing && existing.publish);
    if (!existing) { M.sel = W.keys.slice(); renderSelection(); }
    ov.hidden = false;
  }

  function wzCollect() {
    var W = S.wz;
    var slots = $all("#it-wz-slots tr").map(function (tr) {
      var ins = $all("input", tr);
      return { sid: tr.dataset.sid || undefined, start: ins[0].value, end: ins[1].value };
    });
    if (slots.some(function (s) { return !s.start || !s.end; })) throw new Error("Plage horaire incomplete");
    var body = {
      street: $("#it-wz-street").value.trim(),
      description: $("#it-wz-desc").value.trim(),
      slots: slots,
    };
    if (W.checkRes) body.street_check = W.checkRes;
    if (!W.edit) {
      var dir = (document.querySelector("input[name='it-wz-dir']:checked") || {}).value || "BOTH_DIRECTIONS";
      // Un seul sens : la polyline suit les fleches (sens ferme)
      var pts = W.pts;
      if (dir === "ONE_DIRECTION") pts = chainPolyline(W.keys).pts;
      Object.assign(body, { layer: mLayer(), doc_id: mDocId(), doc_name: (S.m.doc && S.m.doc.name) || mDocId(),
        rule_keys: W.keys, polyline: pts, direction: dir });
    }
    return body;
  }

  function wzSave(publish) {
    var W = S.wz;
    var body;
    try { body = wzCollect(); } catch (e) { toast("warning", e.message); return; }
    if (!body.street) { toast("warning", "Nom de rue obligatoire"); return; }
    if (!body.description) { toast("warning", "Message aux conducteurs obligatoire"); return; }
    var go = function () {
      var p = W.edit
        ? api("PATCH", "/waze/closures/" + encodeURIComponent(W.edit.uid), { body: body }).then(function (r) {
          if (!r.ok || !publish || W.edit.publish) return r;
          return api("PATCH", "/waze/closures/" + encodeURIComponent(W.edit.uid), { body: { action: "publish" } });
        })
        : api("POST", "/waze/closures", { body: Object.assign(body, { publish: !!publish }) });
      p.then(function (r) {
        if (!r.ok) { toast("error", errMsg(r)); return; }
        $("#it-waze").hidden = true;
        W.arrows = false;
        W.checkRes = null;
        renderSelection();
        toast("success", publish ? "Publiee : visible dans Waze quelques minutes apres le debut de chaque plage" : "Enregistree en brouillon");
        loadWaze();
      }).catch(function () { toast("error", "Reseau indisponible"); });
    };
    if (!publish || (W.edit && W.edit.publish && !W.cfg)) { go(); return; }
    var slotsTxt = body.slots.map(function (s) { return s.start.replace("T", " ") + " -> " + s.end.replace("T", " "); }).join(", ");
    window.showConfirmToast("Publier sur Waze \"" + body.street + "\" (" + slotsTxt + ") ? Les conducteurs Waze seront detournes." +
      (S.wz.cfg && !S.wz.cfg.enabled ? " (La publication generale est coupee : rien ne partira tant qu'elle n'est pas activee.)" : ""),
      { okLabel: "Publier", cancelLabel: "Annuler" }).then(function (ok) { if (ok) go(); });
  }

  function wzCheckStreet() {
    var W = S.wz;
    var res = clear($("#it-wz-check-res"));
    res.textContent = "Verification...";
    api("POST", "/waze/check-street", { body: { polyline: W.pts, street: $("#it-wz-street").value } }).then(function (r) {
      clear(res);
      if (!r.ok) { res.textContent = errMsg(r); return; }
      var b = r.body;
      W.checkRes = { match: b.match, common: b.common, checked_at: b.checked_at };
      res.appendChild(h("div", { className: b.match ? "it-wz-ok" : "it-wz-ko",
        text: b.match ? "OK : Waze connait cette rue aux deux bouts." : "Le nom ne correspond pas a la carte Waze aux deux bouts." }));
      res.appendChild(h("div", { text: "Debut : " + (b.first.join(", ") || "aucune rue Waze a moins de 50 m") }));
      res.appendChild(h("div", { text: "Fin : " + (b.last.join(", ") || "aucune rue Waze a moins de 50 m") }));
      (b.common || []).forEach(function (n) {
        res.appendChild(h("button", { type: "button", className: "it-chip", text: "Utiliser \"" + n + "\"",
          onclick: function () { $("#it-wz-street").value = n; wzCheckStreet(); } }));
      });
    }).catch(function () { res.textContent = "Reseau indisponible"; });
  }

  function wzAction(c, action, confirmMsg) {
    var run = function () {
      api("PATCH", "/waze/closures/" + encodeURIComponent(c.uid), { body: { action: action } }).then(function (r) {
        if (!r.ok) { toast("error", errMsg(r)); return; }
        toast("success", "Fait");
        loadWaze();
      });
    };
    if (!confirmMsg) { run(); return; }
    window.showConfirmToast(confirmMsg, { okLabel: "Confirmer", cancelLabel: "Annuler" }).then(function (ok) { if (ok) run(); });
  }

  function wzDelete(c) {
    window.showConfirmToast("Supprimer la fermeture Waze \"" + c.street + "\" ?", { okLabel: "Supprimer", cancelLabel: "Annuler" })
      .then(function (ok) {
        if (!ok) return;
        api("DELETE", "/waze/closures/" + encodeURIComponent(c.uid)).then(function (r) {
          if (!r.ok) { toast("error", errMsg(r)); return; }
          loadWaze();
        });
      });
  }

  function wzFocus(c) {
    if (c.layer === "events") { S.m.eventId = c.doc_id; } else { S.m.seasonId = c.doc_id; S.m.eventId = null; }
    switchTab("carte");
    fillDocSelects();
    reloadEditorDoc().then(function () {
      if (c.polyline.length) S.m.map.fitBounds(c.polyline, { maxZoom: 17, padding: [60, 60] });
      setTimeout(function () { selectIds(c.rule_keys.filter(function (k) { return S.m.feats[k]; }), false); }, 800);
    });
  }

  function renderWazeTab() {
    var W = S.wz;
    var cfgBox = $("#it-wz-cfg");
    if (!cfgBox) return;
    clear(cfgBox);
    var cfg = W.cfg || {};
    var toggle = h("input", { type: "checkbox" });
    toggle.checked = !!cfg.enabled;
    toggle.addEventListener("change", function () {
      var on = toggle.checked;
      window.showConfirmToast(on ? "Activer la publication vers Waze ? Les fermetures publiees seront envoyees aux conducteurs."
        : "Couper la publication vers Waze ? Waze retirera toutes les fermetures du flux.", { okLabel: on ? "Activer" : "Couper", cancelLabel: "Annuler" })
        .then(function (ok) {
          if (!ok) { toggle.checked = !on; return; }
          api("PUT", "/waze/config", { body: { enabled: on } }).then(function (r) {
            if (!r.ok) { toast("error", errMsg(r)); toggle.checked = !on; return; }
            W.cfg = r.body;
            renderWazeTab();
          });
        });
    });
    cfgBox.appendChild(h("div", { className: "it-wz-cfg-row" }, [
      h("label", { className: "it-check it-wz-switch" }, [toggle, h("strong", { text: "Publication vers Waze " + (cfg.enabled ? "ACTIVE" : "COUPEE") })]),
      h("span", { className: "it-badge " + (cfg.feed_auth_ok ? "is-on" : "is-err"), text: cfg.feed_auth_ok ? "Flux protege" : "Identifiants du flux absents" }),
      h("span", { className: "it-badge " + (cfg.geocode_ok ? "is-on" : "is-warn"), text: cfg.geocode_ok ? "Verification des noms OK" : "Jeton de noms de rue absent" }),
      h("a", { href: API + "/waze/preview", target: "_blank", rel: "noopener", className: "btn btn-secondary btn-sm" }, [icon("code"), "Voir le flux"]),
    ]));
    if (cfg.feed_url) {
      cfgBox.appendChild(h("div", { className: "it-hint" }, ["Adresse a declarer dans Waze (Partner Hub > Toolbox > Partner feed, format CIFS XML, authentification Basic, identifiant ",
        h("code", { text: cfg.feed_user || "?" }), ") : ", h("code", { text: cfg.feed_url })]));
    }

    var t = clear($("#it-wz-list"));
    t.appendChild(h("thead", null, h("tr", null, [h("th", { text: "Rue" }), h("th", { text: "Document" }), h("th", { text: "Plages" }),
      h("th", { text: "Statut" }), h("th", null)])));
    var tb = h("tbody");
    if (!W.closures.length) tb.appendChild(h("tr", null, h("td", { colspan: "5", className: "it-empty", text: "Aucune fermeture Waze." })));
    W.closures.forEach(function (c) {
      var st = WZ_STATUS[c.status] || [c.status, "is-off"];
      var plages = c.slots.map(function (s) { return s.start.replace("T", " ") + " -> " + s.end.replace("T", " ") + (s.ended_early ? " (rouverte)" : ""); });
      var acts = [iconBtn("map", "Voir sur la carte", function () { wzFocus(c); }),
        iconBtn("edit", "Modifier", function () { wzFocus(c); setTimeout(function () { openWazeDialog(c); }, 900); })];
      if (c.status === "en_cours") acts.push(iconBtn("lock_open", "Rouvrir maintenant", function () {
        wzAction(c, "reopen", "Rouvrir maintenant \"" + c.street + "\" ? Waze levera la fermeture en cours.");
      }));
      if (c.status === "brouillon") acts.push(iconBtn("campaign", "Publier", function () {
        wzAction(c, "publish", "Publier \"" + c.street + "\" sur Waze ? Les conducteurs seront detournes pendant les plages.");
      }));
      if (c.status === "programmee") acts.push(iconBtn("unpublished", "Retirer la publication", function () { wzAction(c, "unpublish"); }));
      acts.push(iconBtn("delete", "Supprimer", function () { wzDelete(c); }, c.publish && c.status !== "terminee", true));
      tb.appendChild(h("tr", null, [
        h("td", null, [h("strong", { text: c.street }), h("div", { className: "it-hint", text: c.description + " - " +
          (c.direction === "ONE_DIRECTION" ? "un sens" : "deux sens") + " - " + Math.round((c.length_m || 0)) + " m" })]),
        h("td", { text: c.doc_name || c.doc_id }),
        h("td", { className: "it-sub", text: plages.join(" | ") }),
        h("td", null, [h("span", { className: "it-badge " + st[1], text: st[0] }),
          c.status === "desynchronisee" ? h("div", { className: "it-hint", text: "Troncons rouverts dans Cockpit : retiree du flux" }) : null]),
        h("td", null, h("div", { className: "it-actions" }, acts)),
      ]));
    });
    t.appendChild(tb);
  }

  function wireWaze() {
    $("#it-wz-refresh").addEventListener("click", function () {
      api("POST", "/waze/resync").then(loadWaze);
    });
    $("#it-wz-desc").addEventListener("input", function (e) { $("#it-wz-desc-count").textContent = e.target.value.length + " / 40"; });
    $("#it-wz-check").addEventListener("click", wzCheckStreet);
    $("#it-wz-add").addEventListener("click", function () {
      var d = wzDefaultSlot();
      $("#it-wz-slots").appendChild(wzSlotRow(d));
    });
    $all("input[name='it-wz-dir']").forEach(function (r) {
      r.addEventListener("change", function () {
        var one = r.value === "ONE_DIRECTION" && r.checked;
        S.wz.arrows = one;
        $("#it-wz-flip").hidden = !one;
        renderSelection();
      });
    });
    $("#it-wz-flip").addEventListener("click", function () { S.m.flip = !S.m.flip; renderSelection(); });
    $("#it-wz-draft").addEventListener("click", function () { wzSave(false); });
    $("#it-wz-publish").addEventListener("click", function () { wzSave(true); });
    $all("#it-waze [data-close]").forEach(function (b) {
      b.addEventListener("click", function () { S.wz.arrows = false; S.wz.checkRes = null; renderSelection(); });
    });
  }

  // ------------------------------------------------------------------
  // Onglets et branchements
  // ------------------------------------------------------------------

  function switchTab(tab) {
    if (["seasons", "events", "carte", "waze", "guide"].indexOf(tab) < 0) tab = "carte";
    S.tab = tab;
    $all(".it-tab").forEach(function (b) { b.classList.toggle("is-active", b.getAttribute("data-tab") === tab); });
    $all(".it-pane").forEach(function (p) { p.hidden = p.getAttribute("data-pane") !== tab; });
    try { localStorage.setItem("it-admin-tab", tab); } catch (e) { /* stockage indisponible */ }
    if (tab === "carte") { initMap(); editorDefault(); }
    if (tab === "waze") api("POST", "/waze/resync").then(loadWaze);
  }

  function wire() {
    $all(".it-tab").forEach(function (b) {
      b.addEventListener("click", function () { switchTab(b.getAttribute("data-tab")); });
    });
    $all("[data-new]").forEach(function (b) {
      b.addEventListener("click", function () { openDocModal(b.getAttribute("data-new"), "create"); });
    });
    $all(".it-overlay").forEach(function (ov) {
      $all("[data-close]", ov).forEach(function (b) {
        b.addEventListener("click", function () {
          ov.hidden = true;
          if (ov.id === "it-load" && S.loadTimer) { clearTimeout(S.loadTimer); S.loadTimer = null; }
        });
      });
    });
    $("#it-open-load").addEventListener("click", openLoad);
    $("#it-pending-load").addEventListener("click", openLoad);
    $("#it-load-confirm").addEventListener("change", function (e) { $("#it-load-go").disabled = !e.target.checked; });
    $("#it-load-go").addEventListener("click", startLoad);
    $("#it-doc-save").addEventListener("click", saveDocModal);
    $("#it-hist-a").addEventListener("change", renderHistDiff);
    $("#it-hist-b").addEventListener("change", renderHistDiff);
    $("#it-hist-restore").addEventListener("click", restoreVersion);

    $("#it-m-season").addEventListener("change", function (e) {
      S.m.seasonId = e.target.value || null;
      clearSelection();
      reloadEditorDoc();
    });
    $("#it-m-event").addEventListener("change", function (e) {
      S.m.eventId = e.target.value || null;
      clearSelection();
      reloadEditorDoc();
    });
    $("#it-m-minor").addEventListener("change", function () {
      Object.keys(S.m.feats).forEach(function (id) { drawFeature(S.m.feats[id]); });
    });
    $("#it-m-barriers").addEventListener("change", syncBarriersVisibility);
    $("#it-m-test").addEventListener("click", function () { toggleTest(!S.m.test); });
    $("#it-m-q").addEventListener("input", onSearchInput);
    $("#it-m-q").addEventListener("keydown", function (e) {
      if (e.key === "Escape") $("#it-m-results").hidden = true;
    });
    document.addEventListener("click", function (e) {
      if (!e.target.closest || !e.target.closest(".it-ed-search")) $("#it-m-results").hidden = true;
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && S.tab === "carte" && !e.target.closest(".it-overlay")) {
        if (S.m.test) toggleTest(false); else if (S.m.sel.length) clearSelection();
      }
    });
  }

  function init() {
    wire();
    wireWaze();
    loadWaze();
    var tab = "carte";
    try { tab = localStorage.getItem("it-admin-tab") || "carte"; } catch (e) { /* stockage indisponible */ }
    Promise.all([loadStatus(), loadLists()]).then(function () { switchTab(tab); });
    S.statusTimer = setInterval(function () {
      if (!document.hidden && !S.loadTimer) loadStatus();
    }, STATUS_POLL_MS);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
