// alfred_admin.js - Section "Alfred & WhatsApp" de la page Field dispatch.
// IIFE autonome calquee sur vision_admin.js : ses propres helpers HTTP, son
// state, ses modales. Pilote les endpoints /api/alfred/*.

(function () {
  "use strict";

  // ---------- HTTP helpers ----------
  function $(sel, root) { return (root || document).querySelector(sel); }
  function $$(sel, root) { return Array.from((root || document).querySelectorAll(sel)); }
  function csrfToken() {
    var m = document.querySelector('meta[name="csrf-token"]');
    return m ? m.getAttribute("content") : "";
  }
  function apiGet(url) {
    return fetch(url, { headers: { "Accept": "application/json" } })
      .then(function (r) { return r.json(); });
  }
  function apiPost(url, data) {
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken() },
      body: JSON.stringify(data || {}),
    }).then(function (r) {
      return r.json().then(function (b) { return { ok: r.ok, status: r.status, body: b }; });
    });
  }
  function apiDelete(url) {
    return fetch(url, {
      method: "DELETE",
      headers: { "X-CSRFToken": csrfToken() },
    }).then(function (r) {
      return r.json().then(function (b) { return { ok: r.ok, status: r.status, body: b }; });
    });
  }
  function toast(type, msg) {
    if (typeof showToast === "function") return showToast(msg, type);
    if (type === "error") console.error("[alfred]", msg);
    else console.log("[alfred]", msg);
  }
  function escapeHtml(s) {
    if (s === null || s === undefined) return "";
    return String(s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }
  function formatRelative(iso) {
    if (!iso) return "-";
    try {
      var d = new Date(iso);
      var diff = (Date.now() - d.getTime()) / 1000;
      if (diff < 60) return "il y a " + Math.round(diff) + "s";
      if (diff < 3600) return "il y a " + Math.round(diff / 60) + " min";
      if (diff < 86400) return "il y a " + Math.round(diff / 3600) + " h";
      return d.toLocaleString("fr-FR", { dateStyle: "short", timeStyle: "short" });
    } catch (e) { return iso; }
  }

  // ---------- State ----------
  var state = {
    groups: [],          // [{chat_id, chat_name, listen, respond_mentions, summary_enabled, ...}]
    summariesIndex: [],  // pour la modale "historique"
    summaryDetailCache: {},
  };

  // ---------- Rendu table groupes ----------
  function renderGroupsTable() {
    var tbody = $("#alfred-groups-tbody");
    if (!tbody) return;
    if (!state.groups.length) {
      tbody.innerHTML = '<tr><td colspan="8" style="text-align:center; color:var(--muted); padding:16px;">Aucun groupe WhatsApp synchronise. Va dans Centrale d\'alertes > WhatsApp pour synchroniser.</td></tr>';
      var cnt = $("#alfred-admin-count");
      if (cnt) cnt.textContent = "0 groupe";
      return;
    }
    var rows = state.groups.map(function (g) {
      var kw = (g.keyword_rules || []).length;
      var lastSum = g.last_summary_at ? formatRelative(g.last_summary_at) : "-";
      var interval = g.summary_interval_min || 20;
      // Resume non cochable si Ecoute n'est pas active (le backend force aussi
      // summary_enabled=false dans ce cas, le UI reflete la regle).
      var sumDisabled = g.listen ? "" : "disabled";
      var sumTitle = g.listen ? "" : 'title="Activer l\'ecoute d\'abord"';
      return [
        '<tr data-chat-id="', escapeHtml(g.chat_id), '">',
          '<td><strong>', escapeHtml(g.chat_name || g.chat_id), '</strong>',
            '<div style="font-size:11px; color:var(--muted); font-family:monospace;">', escapeHtml(g.chat_id), '</div>',
          '</td>',
          '<td style="text-align:center;"><input type="checkbox" class="alfred-toggle" data-key="listen" ', g.listen ? "checked" : "", '></td>',
          '<td style="text-align:center;"><input type="checkbox" class="alfred-toggle" data-key="respond_mentions" ', g.respond_mentions ? "checked" : "", '></td>',
          '<td style="text-align:center;"><input type="checkbox" class="alfred-toggle" data-key="summary_enabled" ', g.summary_enabled ? "checked" : "", ' ', sumDisabled, ' ', sumTitle, '></td>',
          '<td style="text-align:center; white-space:nowrap;"><input type="number" class="alfred-interval form-input" min="5" max="240" step="5" value="', interval, '" style="width:52px; padding:2px 4px; font-size:12px; display:inline-block; vertical-align:middle;"> <span style="font-size:11px; color:var(--muted);">min</span></td>',
          '<td><button class="btn btn-secondary alfred-kw-edit" style="font-size:11px; padding:3px 8px;">', kw, ' regle', kw > 1 ? "s" : "", '...</button></td>',
          '<td style="font-size:11px; color:var(--muted);">', escapeHtml(lastSum), '</td>',
          '<td style="text-align:right; white-space:nowrap;">',
            '<button class="btn btn-secondary alfred-summarize-now" title="Forcer un resume immediat" style="font-size:11px; padding:3px 8px;">',
              '<span class="material-symbols-outlined" style="font-size:13px; vertical-align:middle;">play_arrow</span> Resumer',
            '</button>',
            ' ',
            '<button class="btn btn-secondary alfred-clear-history" title="Vider l\'historique des messages WhatsApp ingeres pour ce groupe (les resumes deja generes sont conserves)" style="font-size:11px; padding:3px 8px; color:#b91c1c;">',
              '<span class="material-symbols-outlined" style="font-size:13px; vertical-align:middle;">delete_sweep</span> Vider',
            '</button>',
          '</td>',
        '</tr>'
      ].join("");
    });
    tbody.innerHTML = rows.join("");
    var listened = state.groups.filter(function (g) { return g.listen; }).length;
    var c = $("#alfred-admin-count");
    if (c) c.textContent = state.groups.length + " groupe(s) -- " + listened + " ecoute(s)";

    // Bindings
    $$(".alfred-toggle", tbody).forEach(function (cb) {
      cb.addEventListener("change", onToggleChange);
    });
    $$(".alfred-interval", tbody).forEach(function (inp) {
      inp.addEventListener("change", onIntervalChange);
    });
    $$(".alfred-kw-edit", tbody).forEach(function (btn) {
      btn.addEventListener("click", onOpenKeywords);
    });
    $$(".alfred-summarize-now", tbody).forEach(function (btn) {
      btn.addEventListener("click", onSummarizeNow);
    });
    $$(".alfred-clear-history", tbody).forEach(function (btn) {
      btn.addEventListener("click", onClearHistory);
    });
  }

  function getGroupByChatId(chatId) {
    for (var i = 0; i < state.groups.length; i++) {
      if (state.groups[i].chat_id === chatId) return state.groups[i];
    }
    return null;
  }

  function saveGroup(g) {
    return apiPost("/api/alfred/config/" + encodeURIComponent(g.chat_id), {
      chat_name: g.chat_name,
      listen: !!g.listen,
      respond_mentions: !!g.respond_mentions,
      summary_enabled: !!g.summary_enabled,
      summary_interval_min: parseInt(g.summary_interval_min || 20, 10),
      keyword_rules: g.keyword_rules || [],
    }).then(function (res) {
      if (!res.ok) { throw new Error((res.body && res.body.error) || "save failed"); }
      // Merge la version serveur (last_summary_at peut avoir change)
      if (res.body && res.body.group) {
        Object.keys(res.body.group).forEach(function (k) { g[k] = res.body.group[k]; });
      }
      return g;
    });
  }

  // ---------- Handlers : toggle, interval, summarize ----------
  function onToggleChange(e) {
    var row = e.target.closest("tr");
    if (!row) return;
    var g = getGroupByChatId(row.getAttribute("data-chat-id"));
    if (!g) return;
    var key = e.target.getAttribute("data-key");
    g[key] = e.target.checked;
    // Decocher Ecoute decoche aussi Resume (le backend l'imposerait sinon).
    if (key === "listen" && !g.listen) {
      g.summary_enabled = false;
    }
    saveGroup(g).then(function () {
      toast("success", "Config enregistree");
      renderGroupsTable();
    }).catch(function (err) {
      toast("error", "Echec : " + err.message);
      e.target.checked = !e.target.checked; // rollback visuel
    });
  }

  function onIntervalChange(e) {
    var row = e.target.closest("tr");
    if (!row) return;
    var g = getGroupByChatId(row.getAttribute("data-chat-id"));
    if (!g) return;
    var v = parseInt(e.target.value, 10);
    if (isNaN(v) || v < 5) { e.target.value = g.summary_interval_min || 20; return; }
    g.summary_interval_min = v;
    saveGroup(g).then(function () { toast("success", "Intervalle enregistre"); })
      .catch(function (err) { toast("error", "Echec : " + err.message); });
  }

  function onSummarizeNow(e) {
    var row = e.target.closest("tr");
    if (!row) return;
    var chatId = row.getAttribute("data-chat-id");
    if (!chatId) return;
    var btn = e.currentTarget;
    btn.disabled = true;
    apiPost("/api/alfred/summary/trigger/" + encodeURIComponent(chatId), {}).then(function (res) {
      if (res.ok) { toast("success", "Resume lance, verifier dans ~1 min"); }
      else { toast("error", "Echec : " + ((res.body && res.body.error) || res.status)); }
    }).finally(function () { btn.disabled = false; });
  }

  function onClearHistory(e) {
    var row = e.target.closest("tr");
    if (!row) return;
    var chatId = row.getAttribute("data-chat-id");
    if (!chatId) return;
    var grp = state.groups.find(function (g) { return g.chat_id === chatId; });
    var name = grp ? (grp.chat_name || chatId) : chatId;
    var btn = e.currentTarget;

    var ask = (typeof showConfirmToast === "function")
      ? showConfirmToast(
          "Vider l'historique des messages WhatsApp pour " + name +
          " ? Les resumes deja generes sont conserves. Action irreversible.",
          { type: "warning", okLabel: "Vider", cancelLabel: "Annuler" }
        )
      : Promise.resolve(window.confirm("Vider l'historique pour " + name + " ?"));

    ask.then(function (ok) {
      if (!ok) return;
      btn.disabled = true;
      apiDelete("/api/alfred/history/" + encodeURIComponent(chatId)).then(function (res) {
        if (res.ok) {
          var n = (res.body && res.body.deleted) || 0;
          toast("success", n + " message(s) supprime(s) pour " + name);
        } else {
          toast("error", "Echec : " + ((res.body && res.body.error) || res.status));
        }
      }).finally(function () { btn.disabled = false; });
    });
  }

  // ---------- Modale mots-cles ----------
  function onOpenKeywords(e) {
    var row = e.target.closest("tr");
    if (!row) return;
    var chatId = row.getAttribute("data-chat-id");
    var g = getGroupByChatId(chatId);
    if (!g) return;
    $("#alfred-kw-chatname").textContent = g.chat_name || chatId;
    $("#alfred-kw-chat-id").value = chatId;
    renderKwRules(g.keyword_rules || []);
    showModal("#alfred-kw-modal");
  }

  function renderKwRules(rules) {
    var box = $("#alfred-kw-rules-list");
    box.innerHTML = "";
    rules.forEach(function (r) { box.appendChild(buildKwRow(r)); });
    if (!rules.length) {
      var hint = document.createElement("p");
      hint.style.cssText = "color:var(--muted); font-size:12px; margin:4px 0;";
      hint.textContent = "Aucun mot-cle. Ajoute des regex (ex. \"incendie\", \"\\\\bbless[eé]\\\\b\").";
      box.appendChild(hint);
    }
  }

  function buildKwRow(r) {
    r = r || {};
    var row = document.createElement("div");
    row.className = "alfred-kw-row";
    row.style.cssText = "display:grid; grid-template-columns: 1fr 100px 100px 32px; gap:6px; align-items:center;";
    row.dataset.id = r.id || "";
    row.innerHTML = [
      '<input class="form-input kw-regex" placeholder="regex (ex: incendie|fum[eé]e)" value="', escapeHtml(r.regex || ""), '" style="font-family:monospace; font-size:12px;">',
      '<input class="form-input kw-label" placeholder="libelle" value="', escapeHtml(r.label || ""), '" style="font-size:12px;">',
      '<select class="form-input kw-priority" style="font-size:12px;">',
        ['<option value="1">P1 critique</option>',
         '<option value="2">P2 majeur</option>',
         '<option value="3" selected>P3 standard</option>',
         '<option value="4">P4 info</option>'].join(""),
      '</select>',
      '<button class="btn btn-secondary kw-del" style="color:#dc2626; padding:2px 6px;" title="Supprimer">',
        '<span class="material-symbols-outlined" style="font-size:16px;">delete</span>',
      '</button>',
    ].join("");
    var sel = row.querySelector(".kw-priority");
    if (sel) sel.value = String(r.priority || 3);
    row.querySelector(".kw-del").addEventListener("click", function () { row.remove(); });
    return row;
  }

  function saveKwRules() {
    var chatId = $("#alfred-kw-chat-id").value;
    var g = getGroupByChatId(chatId);
    if (!g) return;
    var rules = [];
    $$("#alfred-kw-rules-list .alfred-kw-row").forEach(function (row) {
      var regex = (row.querySelector(".kw-regex").value || "").trim();
      if (!regex) return;
      rules.push({
        id: row.dataset.id || undefined,
        regex: regex,
        label: (row.querySelector(".kw-label").value || regex).trim(),
        priority: parseInt(row.querySelector(".kw-priority").value, 10) || 3,
        flags: "i",
      });
    });
    g.keyword_rules = rules;
    saveGroup(g).then(function () {
      toast("success", "Mots-cles enregistres");
      hideModal("#alfred-kw-modal");
      renderGroupsTable();
    }).catch(function (err) { toast("error", "Echec : " + err.message); });
  }

  // ---------- Modale historique resumes ----------
  function openSummariesModal() {
    // Peuple le selecteur de groupe
    var sel = $("#alfred-sum-filter");
    sel.innerHTML = '<option value="">Tous les groupes</option>';
    state.groups.forEach(function (g) {
      var opt = document.createElement("option");
      opt.value = g.chat_id;
      opt.textContent = g.chat_name || g.chat_id;
      sel.appendChild(opt);
    });
    sel.onchange = function () { loadSummaries(sel.value); };
    showModal("#alfred-sum-modal");
    loadSummaries("");
  }

  function loadSummaries(chatId) {
    var url = "/api/alfred/summaries" + (chatId ? "?chat_id=" + encodeURIComponent(chatId) : "");
    $("#alfred-sum-list").innerHTML = '<li style="color:var(--muted); font-size:12px;">Chargement...</li>';
    $("#alfred-sum-detail").innerHTML = '<p style="color:var(--muted);">Selectionner un resume a gauche.</p>';
    apiGet(url).then(function (r) {
      if (!r.ok) { $("#alfred-sum-list").innerHTML = '<li style="color:#dc2626;">Erreur</li>'; return; }
      state.summariesIndex = r.summaries || [];
      var ul = $("#alfred-sum-list");
      if (!state.summariesIndex.length) {
        ul.innerHTML = '<li style="color:var(--muted); font-size:12px;">Aucun resume</li>';
        return;
      }
      ul.innerHTML = "";
      state.summariesIndex.forEach(function (s) {
        var li = document.createElement("li");
        li.style.cssText = "border:1px solid var(--border); padding:6px 8px; border-radius:4px; cursor:pointer; font-size:12px;";
        li.innerHTML = [
          '<strong>', escapeHtml(s.chat_name || s.chat_id), '</strong>',
          '<div style="color:var(--muted); font-size:11px;">',
            formatRelative(s.created_at), ' &middot; ', (s.msg_count || 0), ' msg',
          '</div>',
        ].join("");
        li.addEventListener("click", function () { loadSummaryDetail(s._id); });
        ul.appendChild(li);
      });
    });
  }

  function loadSummaryDetail(sid) {
    var detail = $("#alfred-sum-detail");
    detail.innerHTML = '<p style="color:var(--muted);">Chargement...</p>';
    apiGet("/api/alfred/summaries/" + encodeURIComponent(sid)).then(function (r) {
      if (!r.ok || !r.summary) { detail.innerHTML = '<p style="color:#dc2626;">Erreur</p>'; return; }
      var s = r.summary;
      var text = (s.raw_text || "").trim();
      detail.innerHTML = [
        '<div style="font-size:12px; color:var(--muted); margin-bottom:8px;">',
          '<strong>', escapeHtml(s.chat_name || s.chat_id), '</strong> &middot; ',
          (s.msg_count || 0), ' messages &middot; ',
          formatRelative(s.created_at),
        '</div>',
        '<pre style="white-space:pre-wrap; font-family:inherit; font-size:13px; margin:0;">',
          escapeHtml(text),
        '</pre>',
      ].join("");
    });
  }

  // ---------- Whitelist DM ----------
  function loadDmWhitelist() {
    return apiGet("/api/alfred/dm-whitelist").then(function (r) {
      if (!r || !r.ok) return;
      renderDmTable(r.entries || []);
    });
  }

  function renderDmTable(entries) {
    var tbody = $("#alfred-dm-tbody");
    if (!tbody) return;
    if (!entries.length) {
      tbody.innerHTML = '<tr><td colspan="5" style="text-align:center; color:var(--muted); padding:12px;">Aucun contact autorise. Alfred ignore tous les DMs avec un message poli.</td></tr>';
      return;
    }
    tbody.innerHTML = entries.map(function (e) {
      return [
        '<tr data-chatid="', escapeHtml(e.chat_id), '">',
          '<td>', escapeHtml(e.label || "-"), '</td>',
          '<td style="font-family:monospace; font-size:11px; color:var(--muted);">', escapeHtml(e.chat_id), '</td>',
          '<td style="font-size:11px; color:var(--muted);">', e.added_at ? formatRelative(e.added_at) : "-", '</td>',
          '<td style="font-size:11px; color:var(--muted);">', escapeHtml(e.added_by || "-"), '</td>',
          '<td style="text-align:right;">',
            '<button class="btn btn-secondary alfred-dm-del" style="color:#dc2626; padding:2px 8px; font-size:11px;" title="Retirer">',
              '<span class="material-symbols-outlined" style="font-size:14px;">delete</span>',
            '</button>',
          '</td>',
        '</tr>'
      ].join("");
    }).join("");
    $$(".alfred-dm-del", tbody).forEach(function (btn) {
      btn.addEventListener("click", onRemoveDm);
    });
  }

  function onAddDmClick() {
    $("#alfred-dm-label").value = "";
    $("#alfred-dm-chatid").value = "";
    showModal("#alfred-dm-modal");
    setTimeout(function () { $("#alfred-dm-chatid").focus(); }, 50);
  }

  function saveDm() {
    var chatId = ($("#alfred-dm-chatid").value || "").trim();
    var label = ($("#alfred-dm-label").value || "").trim();
    if (!chatId) {
      toast("error", "Chat ID requis");
      return;
    }
    apiPost("/api/alfred/dm-whitelist", { chat_id: chatId, label: label }).then(function (r) {
      if (!r.ok) {
        toast("error", "Echec : " + ((r.body && r.body.error) || r.status));
        return;
      }
      toast("success", "Contact autorise");
      hideModal("#alfred-dm-modal");
      loadDmWhitelist();
    });
  }

  function onRemoveDm(e) {
    var row = e.target.closest("tr");
    if (!row) return;
    var chatId = row.getAttribute("data-chatid");
    if (!chatId) return;
    showConfirmToast("Retirer ce contact de la whitelist DM ?", {
      type: "warning", okLabel: "Retirer", cancelLabel: "Annuler"
    }).then(function (ok) {
      if (!ok) return;
      apiDelete("/api/alfred/dm-whitelist/" + encodeURIComponent(chatId)).then(function (r) {
        if (!r.ok) {
          toast("error", "Echec : " + ((r.body && r.body.error) || r.status));
          return;
        }
        toast("success", "Contact retire");
        loadDmWhitelist();
      });
    });
  }

  // ---------- Modale helpers ----------
  function showModal(sel) {
    var m = $(sel); if (!m) return;
    m.hidden = false;
    m.querySelectorAll("[data-close]").forEach(function (btn) {
      btn.onclick = function () { hideModal(sel); };
    });
    var x = m.querySelector(".crud-modal-close");
    if (x) x.onclick = function () { hideModal(sel); };
  }
  function hideModal(sel) {
    var m = $(sel); if (m) m.hidden = true;
  }

  // ---------- Boot ----------
  function loadGroups() {
    return apiGet("/api/alfred/config").then(function (r) {
      if (!r || !r.ok) {
        toast("error", "Echec chargement config Alfred");
        return;
      }
      state.groups = r.groups || [];
      renderGroupsTable();
    });
  }

  function bindGlobal() {
    var btn = $("#alfred-refresh");
    if (btn) btn.addEventListener("click", function () { loadGroups(); loadDmWhitelist(); });
    var syncBtn = $("#alfred-sync-wa");
    if (syncBtn) syncBtn.addEventListener("click", function () {
      showConfirmToast(
        "Re-synchroniser les groupes depuis WAHA ? Les groupes qui n'existent plus cote WhatsApp (ex. apres un changement de numero) seront supprimes de Cockpit.",
        { type: "warning", okLabel: "Resync", cancelLabel: "Annuler" }
      ).then(function (ok) {
        if (!ok) return;
        syncBtn.disabled = true;
        var orig = syncBtn.innerHTML;
        syncBtn.innerHTML = '<span class="material-symbols-outlined" style="font-size:16px; vertical-align:middle; animation:spin 1s linear infinite;">sync</span> Sync en cours...';
        apiPost("/api/whatsapp/groups/sync", {}).then(function (r) {
          if (r.ok) {
            var msg = "Sync : " + (r.body.synced || 0) + " groupes";
            if (r.body.purged) msg += ", " + r.body.purged + " obsoletes supprimes";
            if (r.body.warning) msg += " (" + r.body.warning + ")";
            toast("success", msg);
            loadGroups();
          } else {
            toast("error", "Echec sync : " + ((r.body && r.body.error) || r.status));
          }
        }).finally(function () { syncBtn.disabled = false; syncBtn.innerHTML = orig; });
      });
    });
    var openSum = $("#alfred-summaries-open");
    if (openSum) openSum.addEventListener("click", openSummariesModal);
    var addKw = $("#alfred-kw-add-rule");
    if (addKw) addKw.addEventListener("click", function () {
      $("#alfred-kw-rules-list").appendChild(buildKwRow({}));
    });
    var saveKw = $("#alfred-kw-save");
    if (saveKw) saveKw.addEventListener("click", saveKwRules);
    var addDm = $("#alfred-dm-add-btn");
    if (addDm) addDm.addEventListener("click", onAddDmClick);
    var saveDmBtn = $("#alfred-dm-save");
    if (saveDmBtn) saveDmBtn.addEventListener("click", saveDm);
  }

  document.addEventListener("DOMContentLoaded", function () {
    if (!$("#alfred-groups-table")) return; // section absente : on est sur une autre page
    bindGlobal();
    loadGroups();
    loadDmWhitelist();
  });
})();
