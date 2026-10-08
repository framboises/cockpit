/* Chat Alfred : bulle flottante et fenetre de discussion des operateurs.
 *
 * Injecte par _sidebar.html dans #alfred-chat-root quand un groupe de
 * l'utilisateur autorise le chat. Parle uniquement a /api/alfred-chat/*
 * (Cockpit), jamais a la VM : le secret HMAC reste cote serveur.
 *
 * La question part en POST, la reponse est relue par polling : Alfred met
 * 3 a 90 s a repondre et Cockpit ne bloque aucun thread pendant ce temps
 * (cf. alfred_chat.py). La conversation vit en base : on peut changer de
 * page pendant qu'Alfred reflechit, la reponse attend a l'arrivee.
 */
(function () {
  "use strict";

  var root = document.getElementById("alfred-chat-root");
  if (!root || root.getAttribute("data-ready")) return;
  root.setAttribute("data-ready", "1");

  var API = "/api/alfred-chat";
  var POLL_MS = 1500;
  var POLL_HIDDEN_MS = 5000;
  var HEALTH_MS = 60000;
  var LS_KEY = "alfred_chat_ui";
  var MAX_LEN = 2000;

  var SUGGESTIONS = [
    { icon: "radar", label: "Point de situation", q: "Fais-moi un point de situation." },
    { icon: "assignment", label: "Fiches en cours", q: "Quelles sont les fiches main courante en cours ?" },
    { icon: "traffic", label: "Trafic", q: "Quel est l'etat du trafic autour du site ?" },
    { icon: "partly_cloudy_day", label: "Meteo", q: "Quelle meteo pour les prochaines heures ?" },
    { icon: "schedule", label: "Prochaines echeances", q: "Quelles sont les prochaines echeances de la timeline ?" },
    { icon: "menu_book", label: "Procedure", q: "Quelle est la procedure en cas de malaise d'un spectateur ?" }
  ];

  var SOURCE_ICONS = {
    cockpit_lieux: "door_front", cockpit_frequentation: "groups", cockpit_situation: "radar", cockpit_main_courante: "assignment", cockpit_agenda: "calendar_month", cockpit_main_courante_fiches: "assignment",
    cockpit_main_courante_fiche: "description", cockpit_main_courante_compteurs: "tag",
    cockpit_trafic: "traffic", cockpit_meteo: "partly_cloudy_day", cockpit_alertes: "notifications",
    cockpit_timeline: "schedule", cockpit_presents: "groups", cockpit_wiki_procedures: "menu_book",
    cockpit_evenement: "event"
  };

  var state = {
    open: false, large: false, view: "chat", // chat | history | archive
    messages: [], sessionId: null, busy: false, pendingId: null,
    pollTimer: null, healthTimer: null, tickTimer: null, health: null,
    unread: 0, loaded: false, archive: null
  };

  // ---------- utilitaires ----------

  function lsGet() {
    try { return JSON.parse(localStorage.getItem(LS_KEY) || "{}") || {}; } catch (e) { return {}; }
  }
  function lsSet(patch) {
    try {
      var cur = lsGet();
      Object.keys(patch).forEach(function (k) { cur[k] = patch[k]; });
      localStorage.setItem(LS_KEY, JSON.stringify(cur));
    } catch (e) { /* stockage indisponible : sans consequence */ }
  }

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  function icon(name) { return el("span", "material-symbols-outlined", name); }

  function csrf() {
    var m = document.querySelector('meta[name="csrf-token"]');
    return m ? m.getAttribute("content") || "" : "";
  }

  function refreshCsrf() {
    return fetch("/api/csrf-token", { credentials: "same-origin", cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (j) {
        if (!j || !j.csrf_token) return false;
        var m = document.querySelector('meta[name="csrf-token"]');
        if (!m) {
          m = document.createElement("meta");
          m.setAttribute("name", "csrf-token");
          document.head.appendChild(m);
        }
        m.setAttribute("content", j.csrf_token);
        return true;
      }).catch(function () { return false; });
  }

  function api(method, path, body, retried) {
    var opts = { method: method, credentials: "same-origin", headers: {} };
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    if (method !== "GET") opts.headers["X-CSRFToken"] = csrf();
    return fetch(API + path, opts).then(function (r) {
      return r.json().catch(function () { return { ok: false, error: "http_" + r.status }; })
        .then(function (j) {
          j = j || {};
          j._status = r.status;
          if (!retried && r.status === 400 && j.code === "csrf") {
            return refreshCsrf().then(function (ok) {
              return ok ? api(method, path, body, true) : j;
            });
          }
          return j;
        });
    }).catch(function () { return { ok: false, error: "reseau", _status: 0 }; });
  }

  function pageContext() {
    var ev = window.selectedEvent, yr = window.selectedYear;
    if (!ev) { try { ev = localStorage.getItem("cockpit_event"); } catch (e) { ev = null; } }
    if (!yr) { try { yr = localStorage.getItem("cockpit_year"); } catch (e) { yr = null; } }
    var title = (document.title || "").replace(/\s*[-|·]\s*COCKPIT.*$/i, "").trim();
    return { event: ev || "", year: yr ? String(yr) : "", page: title || location.pathname,
             path: location.pathname };
  }

  function fmtTime(iso) {
    if (!iso) return "";
    var d = new Date(iso);
    if (isNaN(d)) return "";
    return d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
  }
  function fmtDay(iso) {
    var d = new Date(iso);
    if (isNaN(d)) return "";
    var today = new Date();
    var y = new Date(); y.setDate(today.getDate() - 1);
    if (d.toDateString() === today.toDateString()) return "Aujourd'hui";
    if (d.toDateString() === y.toDateString()) return "Hier";
    return d.toLocaleDateString("fr-FR", { weekday: "long", day: "numeric", month: "long" });
  }
  function fmtDur(ms) {
    if (!ms) return "";
    return (ms / 1000).toFixed(1).replace(".", ",") + " s";
  }

  // Rendu texte -> DOM sur : paragraphes, listes "- ", **gras**. Jamais
  // d'innerHTML sur un contenu venu du modele.
  function renderRich(container, text) {
    var lines = String(text || "").replace(/\r/g, "").split("\n");
    var list = null, para = null;
    function inline(target, s) {
      var parts = s.split(/(\*\*[^*]+\*\*)/g);
      parts.forEach(function (p) {
        if (/^\*\*[^*]+\*\*$/.test(p)) target.appendChild(el("strong", null, p.slice(2, -2)));
        else if (p) target.appendChild(document.createTextNode(p));
      });
    }
    lines.forEach(function (raw) {
      var line = raw.replace(/^#{1,6}\s+/, "");
      var m = /^\s*(?:[-*•]|\d+[.)])\s+(.*)$/.exec(line);
      if (m) {
        para = null;
        if (!list) { list = el("ul"); container.appendChild(list); }
        var li = el("li"); inline(li, m[1]); list.appendChild(li);
        return;
      }
      list = null;
      if (!line.trim()) { para = null; return; }
      if (!para) { para = el("p"); container.appendChild(para); }
      else para.appendChild(el("br"));
      inline(para, line);
    });
  }

  // ---------- squelette ----------

  var fab = el("button", "ac-fab");
  fab.type = "button";
  fab.setAttribute("aria-label", "Ouvrir le chat Alfred (Alt+A)");
  fab.title = "Alfred, assistant du PC Organisation (Alt+A)";
  fab.appendChild(icon("forum"));
  var fabDot = el("span", "ac-fab-dot");
  var fabBadge = el("span", "ac-fab-badge");
  fab.appendChild(fabDot);
  fab.appendChild(fabBadge);

  // Uniquement des <div> : style.css stylise TOUT <footer> de la page
  // (hauteur fixe, flex centre, et position: fixed sur .single-col-page),
  // ce qui ecrasait le pied du widget. Aucune balise semantique ici.
  var panel = el("div", "ac-panel is-closed");
  panel.setAttribute("role", "dialog");
  panel.setAttribute("aria-label", "Chat Alfred");

  var head = el("div", "ac-head");
  var avatar = el("div", "ac-avatar", "A");
  var titleBox = el("div", "ac-title");
  titleBox.appendChild(el("strong", null, "Alfred"));
  var status = el("div", "ac-status");
  status.appendChild(el("i"));
  var statusTxt = el("span", null, "Verification...");
  status.appendChild(statusTxt);
  titleBox.appendChild(status);
  var btns = el("div", "ac-head-btns");
  function headBtn(name, label, onClick, extra) {
    var b = el("button", "ac-icon-btn" + (extra ? " " + extra : ""));
    b.type = "button"; b.title = label; b.setAttribute("aria-label", label);
    b.appendChild(icon(name));
    b.addEventListener("click", onClick);
    btns.appendChild(b);
    return b;
  }
  var histBtn = headBtn("history", "Conversations precedentes", function () { toggleHistory(); });
  headBtn("edit_square", "Nouvelle conversation", function () { newConversation(); });
  var sizeBtn = headBtn("open_in_full", "Agrandir", function () { setLarge(!state.large); }, "ac-size-btn");
  headBtn("remove", "Reduire (Echap)", function () { setOpen(false); });
  head.appendChild(avatar); head.appendChild(titleBox); head.appendChild(btns);

  var body = el("div", "ac-body");
  body.setAttribute("aria-live", "polite");

  var foot = el("div", "ac-foot");
  var compose = el("div", "ac-compose");
  var input = el("textarea", "ac-input");
  input.rows = 1;
  input.maxLength = MAX_LEN;
  input.placeholder = "Posez votre question a Alfred...";
  input.setAttribute("aria-label", "Votre message");
  var send = el("button", "ac-send");
  send.type = "button"; send.title = "Envoyer (Entree)"; send.setAttribute("aria-label", "Envoyer");
  send.appendChild(icon("arrow_upward"));
  compose.appendChild(input); compose.appendChild(send);
  var hint = el("div", "ac-hint");
  var hintLeft = el("span", "ac-hint-l", "Lecture seule, verifiez l'essentiel");
  var hintRight = el("span", "ac-hint-r", "Entree pour envoyer");
  hint.appendChild(hintLeft); hint.appendChild(hintRight);
  foot.appendChild(compose); foot.appendChild(hint);

  panel.appendChild(head); panel.appendChild(body); panel.appendChild(foot);
  root.appendChild(fab);
  root.appendChild(panel);

  // ---------- ouverture / taille ----------

  function setOpen(open) {
    state.open = !!open;
    panel.classList.toggle("is-closed", !state.open);
    fab.classList.toggle("is-hidden", state.open);
    lsSet({ open: state.open });
    if (state.open) {
      state.unread = 0; renderBadge();
      if (!state.loaded) loadState(); else scrollBottom(false);
      checkHealth();
      setTimeout(function () { try { input.focus(); } catch (e) {} }, 220);
    }
  }
  function setLarge(large) {
    state.large = !!large;
    panel.classList.toggle("is-large", state.large);
    sizeBtn.firstChild.textContent = state.large ? "close_fullscreen" : "open_in_full";
    sizeBtn.title = state.large ? "Taille normale" : "Agrandir";
    lsSet({ large: state.large });
  }
  function renderBadge() {
    fabBadge.textContent = state.unread > 9 ? "9+" : String(state.unread || "");
    fabBadge.classList.toggle("is-on", state.unread > 0);
  }

  fab.addEventListener("click", function () { setOpen(true); });
  panel.addEventListener("keydown", function (e) {
    if (e.key === "Escape") { e.stopPropagation(); setOpen(false); fab.focus(); }
  });
  document.addEventListener("keydown", function (e) {
    if (e.altKey && !e.ctrlKey && !e.metaKey && (e.key === "a" || e.key === "A")) {
      e.preventDefault(); setOpen(!state.open);
    }
  });

  // ---------- sante ----------

  function applyHealth(h) {
    state.health = h || null;
    var ok = !!(h && h.ok);
    var known = !!h;
    status.classList.toggle("is-ok", ok);
    status.classList.toggle("is-ko", known && !ok);
    fabDot.classList.toggle("is-ok", ok);
    fabDot.classList.toggle("is-ko", known && !ok);
    if (!known) statusTxt.textContent = "Verification...";
    else if (ok) statusTxt.textContent = "En ligne - IA locale du PC Org";
    else if (h.configured === false) statusTxt.textContent = "Non configure sur ce serveur";
    else if (h.ollama === false) statusTxt.textContent = "Modele indisponible";
    else statusTxt.textContent = "Injoignable";
  }
  function checkHealth() {
    api("GET", "/health").then(function (h) { if (h && h._status === 200) applyHealth(h); });
  }

  // ---------- rendu ----------

  function scrollBottom(smooth) {
    var prev = body.style.scrollBehavior;
    if (!smooth) body.style.scrollBehavior = "auto";
    body.scrollTop = body.scrollHeight;
    body.style.scrollBehavior = prev;
  }

  function welcome() {
    var w = el("div", "ac-welcome");
    w.appendChild(el("div", "ac-avatar", "A"));
    var fn = root.getAttribute("data-firstname") || "";
    w.appendChild(el("h3", null, "Bonjour" + (fn ? " " + fn : "") + ","));
    w.appendChild(el("p", null, "Je consulte pour vous la main courante, le trafic, la meteo, les alertes, la timeline et les procedures. Que puis-je verifier ?"));
    var s = el("div", "ac-suggest");
    SUGGESTIONS.forEach(function (sg) {
      var b = el("button");
      b.type = "button";
      b.appendChild(icon(sg.icon));
      b.appendChild(document.createTextNode(sg.label));
      b.addEventListener("click", function () { ask(sg.q); });
      s.appendChild(b);
    });
    w.appendChild(s);
    return w;
  }

  function typingLabel(started) {
    var s = Math.max(0, Math.round((Date.now() - started) / 1000));
    var txt = s < 4 ? "Alfred reflechit" : s < 20 ? "Alfred consulte Cockpit" : s < 45 ? "Analyse en cours, un instant" : "C'est un peu long, Alfred travaille encore";
    return txt + "... " + s + " s";
  }

  function messageRow(m, readOnly) {
    var isUser = m.role === "user";
    var pending = !isUser && (m.status === "queued" || m.status === "running");
    var isErr = !isUser && m.status === "error";
    var row = el("div", "ac-row " + (isUser ? "is-user" : "is-bot") + (isErr ? " is-error" : ""));
    row.setAttribute("data-id", m.id);
    if (!isUser) row.appendChild(el("div", "ac-avatar is-sm", "A"));
    var col = el("div", "ac-col");
    var bubble = el("div", "ac-bubble");
    if (pending) {
      var t = el("div", "ac-typing");
      var d = el("span", "ac-dots");
      d.appendChild(el("span")); d.appendChild(el("span")); d.appendChild(el("span"));
      var lbl = el("span", "ac-typing-label", typingLabel(Date.parse(m.created_at) || Date.now()));
      lbl.setAttribute("data-started", String(Date.parse(m.created_at) || Date.now()));
      t.appendChild(d); t.appendChild(lbl);
      bubble.appendChild(t);
    } else if (isErr) {
      bubble.appendChild(el("p", null, m.error || "Alfred n'a pas pu repondre."));
      if (!readOnly) {
        var retry = el("button", "ac-retry", "Reessayer");
        retry.type = "button";
        retry.addEventListener("click", function () { retryLast(); });
        bubble.appendChild(retry);
      }
    } else if (isUser) {
      bubble.textContent = m.content;
    } else {
      renderRich(bubble, m.content);
    }
    col.appendChild(bubble);

    if (!pending) {
      var meta = el("div", "ac-meta");
      meta.appendChild(el("span", null, fmtTime(m.created_at)));
      if (!isUser && !isErr) {
        (m.sources || []).forEach(function (s) {
          var c = el("span", "ac-chip");
          c.title = "Donnee consultee : " + s.name;
          c.appendChild(icon(SOURCE_ICONS[s.name] || "database"));
          c.appendChild(document.createTextNode(s.label));
          meta.appendChild(c);
        });
        if (m.duration_ms) meta.appendChild(el("span", null, fmtDur(m.duration_ms)));
        var acts = el("span", "ac-actions" + (m.rating ? " is-rated" : ""));
        var copy = el("button", "ac-mini"); copy.type = "button"; copy.title = "Copier";
        copy.appendChild(icon("content_copy"));
        copy.addEventListener("click", function () {
          try { navigator.clipboard.writeText(m.content); copy.firstChild.textContent = "check"; } catch (e) {}
          setTimeout(function () { copy.firstChild.textContent = "content_copy"; }, 1200);
        });
        acts.appendChild(copy);
        if (!readOnly) {
          [[1, "thumb_up", "Reponse utile"], [-1, "thumb_down", "Reponse fausse ou inutile"]].forEach(function (r) {
            var b = el("button", "ac-mini" + (m.rating === r[0] ? " is-on" : ""));
            b.type = "button"; b.title = r[2];
            b.appendChild(icon(r[1]));
            b.addEventListener("click", function () { rate(m, m.rating === r[0] ? 0 : r[0]); });
            acts.appendChild(b);
          });
        }
        meta.appendChild(acts);
      }
      col.appendChild(meta);
      if (!isUser && !isErr && !readOnly && m.rating === -1) col.appendChild(feedbackBox(m));
    }
    row.appendChild(col);
    return row;
  }

  function renderMessages(list, readOnly, banner) {
    body.innerHTML = "";
    if (banner) body.appendChild(banner);
    if (!list.length && !readOnly) { body.appendChild(welcome()); return; }
    var lastDay = null;
    list.forEach(function (m) {
      var day = fmtDay(m.created_at);
      if (day && day !== lastDay) { body.appendChild(el("div", "ac-day", day)); lastDay = day; }
      body.appendChild(messageRow(m, readOnly));
    });
    scrollBottom(false);
  }

  function render() {
    if (state.view === "chat") {
      foot.style.display = "";
      histBtn.firstChild.textContent = "history";
      renderMessages(state.messages, false);
    }
    updateComposer();
  }

  function updateComposer() {
    var len = input.value.length;
    send.disabled = state.busy || !input.value.trim();
    if (len > MAX_LEN * 0.8) {
      hintRight.textContent = len + " / " + MAX_LEN;
      hintRight.className = "ac-hint-r" + (len >= MAX_LEN ? " is-warn" : "");
    } else {
      hintRight.textContent = state.busy ? "Alfred repond..." : "Entree pour envoyer";
      hintRight.title = "Maj+Entree pour aller a la ligne";
      hintRight.className = "ac-hint-r";
    }
    fab.classList.toggle("is-thinking", state.busy);
  }

  function autosize() {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 140) + "px";
  }

  // ---------- donnees ----------

  function loadState() {
    return api("GET", "/state").then(function (j) {
      if (!j || !j.ok) {
        body.innerHTML = "";
        body.appendChild(el("div", "ac-banner", j && j._status === 403
          ? "Le chat Alfred n'est pas active pour votre groupe."
          : "Impossible de charger la conversation."));
        return;
      }
      state.loaded = true;
      state.sessionId = j.session_id;
      state.messages = j.messages || [];
      applyHealth(j.health);
      var pend = pendingMessage();
      state.busy = !!pend;
      if (state.view === "chat") render();
      if (pend) startPolling(pend.id);
    });
  }

  function pendingMessage() {
    for (var i = state.messages.length - 1; i >= 0; i--) {
      var m = state.messages[i];
      if (m.role === "assistant" && (m.status === "queued" || m.status === "running")) return m;
    }
    return null;
  }

  function replaceMessage(m) {
    for (var i = 0; i < state.messages.length; i++) {
      if (state.messages[i].id === m.id) { state.messages[i] = m; return; }
    }
    state.messages.push(m);
  }

  function ask(text) {
    text = String(text || "").trim();
    if (!text || state.busy) return;
    if (state.view !== "chat") { state.view = "chat"; }
    state.busy = true;
    input.value = ""; autosize();
    var tmp = { id: "tmp-u", role: "user", content: text, status: "done", created_at: new Date().toISOString() };
    var tmpA = { id: "tmp-a", role: "assistant", content: "", status: "queued", created_at: new Date().toISOString() };
    state.messages.push(tmp, tmpA);
    render();
    scrollBottom(true);
    api("POST", "/ask", { content: text, context: pageContext() }).then(function (j) {
      state.messages = state.messages.filter(function (m) { return m.id !== "tmp-u" && m.id !== "tmp-a"; });
      if (!j || !j.ok) {
        state.busy = false;
        var msg = j && j.error === "occupe" ? "Alfred repond deja a votre question precedente."
          : j && j.error === "trop_de_questions" ? "Trop de questions en peu de temps, patientez quelques minutes."
          : j && j.error === "non_autorise" ? "Le chat Alfred n'est pas active pour votre groupe."
          : "Envoi impossible (" + ((j && j.error) || "erreur") + ").";
        state.messages.push({ id: "tmp-u2", role: "user", content: text, status: "done", created_at: tmp.created_at });
        state.messages.push({ id: "err-" + Date.now(), role: "assistant", status: "error", error: msg, created_at: new Date().toISOString() });
        render();
        if (j && j.error === "occupe") loadState();
        return;
      }
      state.sessionId = j.session_id;
      state.messages.push(j.user_message, j.assistant_message);
      render();
      startPolling(j.assistant_message.id);
    });
  }

  function retryLast() {
    for (var i = state.messages.length - 1; i >= 0; i--) {
      if (state.messages[i].role === "user") { ask(state.messages[i].content); return; }
    }
  }

  function startPolling(id) {
    stopPolling();
    state.pendingId = id;
    state.busy = true;
    updateComposer();
    var tick = function () {
      api("GET", "/message/" + encodeURIComponent(id)).then(function (j) {
        if (state.pendingId !== id) return;
        if (j && j.ok && j.message) {
          var m = j.message;
          if (m.status === "queued" || m.status === "running") { schedule(); return; }
          replaceMessage(m);
          state.busy = false; state.pendingId = null;
          if (!state.open) { state.unread += 1; renderBadge(); fab.classList.remove("is-pulse"); void fab.offsetWidth; fab.classList.add("is-pulse"); }
          if (state.view === "chat") { render(); scrollBottom(true); }
          updateComposer();
          if (m.status === "error") checkHealth();
          return;
        }
        if (j && j._status === 404) { state.busy = false; state.pendingId = null; updateComposer(); return; }
        schedule();
      });
    };
    var schedule = function () {
      state.pollTimer = setTimeout(tick, document.hidden ? POLL_HIDDEN_MS : POLL_MS);
    };
    schedule();
  }
  function stopPolling() {
    if (state.pollTimer) clearTimeout(state.pollTimer);
    state.pollTimer = null;
  }

  function newConversation() {
    if (state.busy) {
      if (window.showToast) window.showToast("info", "Attendez la reponse d'Alfred avant de changer de conversation.");
      return;
    }
    api("POST", "/new", {}).then(function (j) {
      if (!j || !j.ok) return;
      state.messages = []; state.sessionId = null; state.view = "chat";
      render();
      input.focus();
    });
  }

  // Motifs du pouce bas (memes cles que alfred_retours.MOTIFS)
  var MOTIFS = [
    ["faux", "Faux"], ["incomplet", "Incomplet"], ["pas_compris", "Question mal comprise"],
    ["mauvais_evenement", "Mauvais événement ou date"], ["trop_long", "Trop long ou confus"],
    ["autre", "Autre"]
  ];

  function feedbackBox(m) {
    var box = el("div", "ac-fb");
    if (m.rating_motif || m._fbSkip) {
      var lib = m.rating_motif;
      MOTIFS.forEach(function (x) { if (x[0] === lib) lib = x[1]; });
      box.className = "ac-fb is-done";
      box.appendChild(icon("flag"));
      box.appendChild(document.createTextNode(lib ? "Signalé : " + lib : "Signalé"));
      if (!m.rating_motif) {
        var add = el("button", "ac-fb-link", "Préciser");
        add.type = "button";
        add.addEventListener("click", function () { m._fbSkip = false; rerender(); });
        box.appendChild(add);
      }
      return box;
    }
    box.appendChild(el("div", "ac-fb-title", "Qu'est-ce qui ne va pas ?"));
    var chips = el("div", "ac-fb-chips");
    var choisi = null;
    var envoyer = el("button", "ac-fb-send", "Envoyer");
    envoyer.type = "button"; envoyer.disabled = true;
    MOTIFS.forEach(function (x) {
      var c = el("button", "ac-fb-chip", x[1]);
      c.type = "button";
      c.addEventListener("click", function () {
        choisi = x[0];
        Array.prototype.forEach.call(chips.children, function (n) { n.classList.toggle("is-on", n === c); });
        envoyer.disabled = false;
      });
      chips.appendChild(c);
    });
    box.appendChild(chips);
    var txt = el("input", "ac-fb-input");
    txt.type = "text"; txt.maxLength = 500;
    txt.placeholder = "Ce qui était attendu (facultatif)";
    txt.addEventListener("keydown", function (e) {
      e.stopPropagation();
      if (e.key === "Enter" && choisi) { e.preventDefault(); envoyer.click(); }
    });
    box.appendChild(txt);
    var acts = el("div", "ac-fb-acts");
    var passer = el("button", "ac-fb-link", "Passer");
    passer.type = "button";
    passer.addEventListener("click", function () { m._fbSkip = true; rerender(); });
    envoyer.addEventListener("click", function () {
      if (!choisi) return;
      envoyer.disabled = true;
      api("POST", "/feedback/" + encodeURIComponent(m.id),
          { rating: -1, motif: choisi, comment: txt.value }).then(function (j) {
        if (!j || !j.ok) { envoyer.disabled = false; return; }
        m.rating_motif = choisi;
        replaceMessage(m);
        rerender();
        if (window.showToast) window.showToast("success", "Merci, le retour est transmis pour amélioration.");
      });
    });
    acts.appendChild(passer); acts.appendChild(envoyer);
    box.appendChild(acts);
    return box;
  }

  function rerender() {
    if (state.view !== "chat") return;
    var top = body.scrollTop;
    render();
    body.scrollTop = top;
  }

  function rate(m, rating) {
    api("POST", "/feedback/" + encodeURIComponent(m.id), { rating: rating }).then(function (j) {
      if (!j || !j.ok) return;
      m.rating = rating || null;
      m.rating_motif = null; m._fbSkip = false;
      replaceMessage(m);
      rerender();
      var fb = rating === -1 ? body.querySelector(".ac-fb:not(.is-done)") : null;
      if (fb && fb.scrollIntoView) fb.scrollIntoView({ block: "nearest", behavior: "smooth" });
    });
  }

  // ---------- historique ----------

  function toggleHistory() {
    if (state.view !== "chat") { state.view = "chat"; render(); return; }
    state.view = "history";
    foot.style.display = "none";
    histBtn.firstChild.textContent = "chat";
    body.innerHTML = "";
    body.appendChild(el("div", "ac-typing-label", "Chargement..."));
    api("GET", "/sessions").then(function (j) {
      if (state.view !== "history") return;
      body.innerHTML = "";
      var box = el("div", "ac-history");
      box.appendChild(el("h4", null, "Vos conversations (90 jours)"));
      var list = (j && j.sessions) || [];
      if (!list.length) box.appendChild(el("div", "ac-typing-label", "Aucune conversation pour l'instant."));
      list.forEach(function (s) {
        var b = el("button", "ac-hist-item");
        b.type = "button";
        b.appendChild(icon(s.current ? "chat_bubble" : "history"));
        var t = el("span", "ac-hist-txt");
        t.appendChild(el("b", null, s.title || "(sans titre)"));
        t.appendChild(el("small", null, (s.current ? "En cours - " : "") + fmtDay(s.updated_at) + " " + fmtTime(s.updated_at) + " - " + s.count + " question" + (s.count > 1 ? "s" : "")));
        b.appendChild(t);
        b.addEventListener("click", function () {
          if (s.current) { state.view = "chat"; render(); return; }
          openArchive(s.id);
        });
        box.appendChild(b);
      });
      body.appendChild(box);
    });
  }

  function openArchive(id) {
    api("GET", "/session/" + encodeURIComponent(id)).then(function (j) {
      if (!j || !j.ok) return;
      state.view = "archive";
      foot.style.display = "none";
      var banner = el("div", "ac-banner");
      banner.appendChild(icon("history"));
      banner.appendChild(document.createTextNode("Conversation archivee (lecture seule)"));
      var back = el("button", null, "Revenir");
      back.type = "button";
      back.addEventListener("click", function () { state.view = "chat"; render(); });
      banner.appendChild(back);
      renderMessages(j.messages || [], true, banner);
    });
  }

  // ---------- saisie ----------

  input.addEventListener("input", function () { autosize(); updateComposer(); });
  input.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      if (!send.disabled) ask(input.value);
    }
  });
  send.addEventListener("click", function () { ask(input.value); });

  // Compteur de secondes de l'indicateur de reflexion
  state.tickTimer = setInterval(function () {
    if (!state.busy || !state.open) return;
    var lbls = body.querySelectorAll(".ac-typing-label[data-started]");
    for (var i = 0; i < lbls.length; i++) {
      lbls[i].textContent = typingLabel(parseInt(lbls[i].getAttribute("data-started"), 10));
    }
  }, 1000);

  state.healthTimer = setInterval(function () { if (state.open) checkHealth(); }, HEALTH_MS);

  // ---------- demarrage ----------

  var saved = lsGet();
  setLarge(!!saved.large);
  // Etat initial : on charge la conversation meme fenetre fermee, pour
  // reprendre le suivi d'une reponse en cours (changement de page) et
  // afficher la pastille de sante sur la bulle.
  loadState().then(function () {
    if (saved.open) setOpen(true);
  });
})();
