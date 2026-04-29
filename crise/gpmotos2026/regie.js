/**
 * regie.js — Régie TV (côté master.html)
 *
 * Module IIFE chargé par master.html sous la route protégée
 * /crise/<exercise>/regie.js (gating équivalent à master.html). Il pilote la
 * vue "Live feed régie" : grille des inputs, message libre, statut TV,
 * panneau "À l'antenne", bouton stopper.
 *
 * Backend : /crise/<exercise>/livefeed/{state,clear,csrf,inputs.json}
 * Voir crise_auth.py pour l'API et la validation des payloads.
 */
(function() {
  'use strict';

  // ===================================================================
  // Config + state global
  // ===================================================================
  var EXERCISE_ID = (function() {
    var m = document.querySelector('meta[name="exercise-id"]');
    return (m && m.getAttribute('content')) || 'gpmotos2026';
  })();
  var BASE = '/crise/' + EXERCISE_ID + '/livefeed';

  var CSRF_TOKEN = '';
  var REGIE_INPUTS = [];        // tableau d'inputs depuis le manifeste
  var REGIE_LAST_VERSION = -1;
  var REGIE_LAST_PAYLOAD = null;
  var REGIE_TV_CLIENTS = [];

  var DEFAULT_DURATION_S = 60;  // duree par defaut sur les cartes inputs
  var DEFAULT_ANNOUNCE = 'alert'; // 'alert' | 'notification' | 'none'
  var STATUS_POLL_MS = 3000;

  function buildAnnounceSelect(defaultValue, idAttr) {
    var sel = el('select', { className: 'regie-announce-select' });
    if (idAttr) sel.id = idAttr;
    sel.dataset.role = 'announce';
    [
      ['alert',        '⚠ Alerte (rouge stressant + son alert)'],
      ['notification', '🔔 Notification (orange doux + son notif)'],
      ['none',         '— Aucune annonce'],
    ].forEach(function(p) {
      var opt = el('option', { value: p[0], text: p[1] });
      if (p[0] === defaultValue) opt.selected = true;
      sel.appendChild(opt);
    });
    return sel;
  }

  // ===================================================================
  // Helpers DOM (zero injection HTML, tout en textContent + appendChild)
  // ===================================================================
  function $(id) { return document.getElementById(id); }
  function el(tag, attrs, children) {
    var n = document.createElement(tag);
    if (attrs) Object.keys(attrs).forEach(function(k) {
      if (k === 'className') n.className = attrs[k];
      else if (k === 'text') n.textContent = attrs[k];
      else if (k.indexOf('on') === 0 && typeof attrs[k] === 'function')
        n.addEventListener(k.slice(2), attrs[k]);
      else n.setAttribute(k, attrs[k]);
    });
    if (children) children.forEach(function(c) {
      if (c == null) return;
      n.appendChild(c.nodeType ? c : document.createTextNode(String(c)));
    });
    return n;
  }
  function clearChildren(n) { while (n && n.firstChild) n.removeChild(n.firstChild); }

  // ===================================================================
  // HTTP helpers
  // ===================================================================
  function fetchCsrfToken() {
    return fetch(BASE + '/csrf', { credentials: 'same-origin', cache: 'no-store' })
      .then(function(r) {
        if (!r.ok) throw new Error('CSRF HTTP ' + r.status);
        return r.json();
      })
      .then(function(j) {
        if (!j || !j.ok || !j.csrf_token) throw new Error('CSRF invalid');
        CSRF_TOKEN = j.csrf_token;
        return CSRF_TOKEN;
      });
  }

  function postJson(path, body, retried) {
    return fetch(BASE + path, {
      method: 'POST',
      credentials: 'same-origin',
      cache: 'no-store',
      headers: {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'X-CSRFToken': CSRF_TOKEN,
      },
      body: JSON.stringify(body || {}),
    }).then(function(r) {
      if ((r.status === 400 || r.status === 403) && !retried) {
        // Probable expiration CSRF -> refetch + retry une fois
        return fetchCsrfToken().then(function() { return postJson(path, body, true); });
      }
      return r.json().then(function(j) { return { status: r.status, json: j }; });
    });
  }

  function getJson(path) {
    return fetch(BASE + path, { credentials: 'same-origin', cache: 'no-store' })
      .then(function(r) { return r.json().then(function(j) { return { status: r.status, json: j }; }); });
  }

  // ===================================================================
  // Manifeste : charge la liste autorisee depuis le serveur
  // ===================================================================
  function loadManifest() {
    return getJson('/inputs.json').then(function(res) {
      if (res.status !== 200 || !res.json || !res.json.ok) {
        throw new Error('Manifeste indisponible (HTTP ' + res.status + ')');
      }
      REGIE_INPUTS = (res.json.inputs || []).slice();
    });
  }

  // ===================================================================
  // Rendu de la vue regie
  // ===================================================================
  function renderRegieShell() {
    var view = $('view-regie');
    if (!view) return;
    clearChildren(view);

    // Event delegation : un seul listener pour gerer les actions, robuste aux
    // re-renders du panneau A l'antenne (qui recree le bouton stop a chaque tick).
    if (!view.dataset.delegationBound) {
      view.addEventListener('click', function(ev) {
        var t = ev.target;
        // Remonte jusqu'au bouton avec data-action
        while (t && t !== view && !(t.dataset && t.dataset.action)) t = t.parentNode;
        if (!t || t === view || !t.dataset || !t.dataset.action) return;
        var action = t.dataset.action;
        if (action === 'stop-broadcast') {
          ev.preventDefault();
          clearBroadcast();
        } else if (action === 'toggle-msg-box') {
          ev.preventDefault();
          toggleMessageBox();
        }
      });
      view.dataset.delegationBound = '1';
    }

    // Header
    view.appendChild(el('div', { className: 'section-header' }, [
      el('h2', { text: 'Live feed régie' }),
      el('p', {
        text: "Console de pilotage du mur d'images TV. Une seule diffusion à la fois. Annonce flash + son avant chaque input."
      })
    ]));

    // Bandeau A l'antenne + statut TV
    var onAir = el('div', { className: 'regie-onair', id: 'regie-onair' });
    view.appendChild(onAir);
    renderOnAir(null);

    // Section message libre
    view.appendChild(renderMessageBox());

    // Grille inputs
    var gridHead = el('div', { className: 'regie-grid-head' });
    gridHead.appendChild(el('h3', { text: 'Inputs disponibles' }));
    gridHead.appendChild(el('p', {
      className: 'regie-grid-hint',
      text: 'Ajustez annonce + durée par carte, puis cliquez Diffuser. La TV affiche immédiatement.'
    }));
    view.appendChild(gridHead);

    var grid = el('div', { className: 'regie-grid', id: 'regie-grid' });
    view.appendChild(grid);
    renderRegieGrid();
  }

  function renderRegieGrid() {
    var grid = $('regie-grid');
    if (!grid) return;
    clearChildren(grid);
    REGIE_INPUTS.forEach(function(inp) {
      grid.appendChild(buildInputCard(inp));
    });
  }

  function buildInputCard(inp) {
    var card = el('div', { className: 'regie-card', 'data-input-id': String(inp.id) });

    // Thumb (reutilise le helper inputThumbNode expose par master.html)
    var thumb = el('div', { className: 'regie-card-thumb' });
    if (typeof window.inputThumbNode === 'function') {
      var node = window.inputThumbNode(inp);
      if (node) thumb.appendChild(node);
    }
    card.appendChild(thumb);

    // Meta
    var meta = el('div', { className: 'regie-card-meta' });
    meta.appendChild(el('div', { className: 'regie-card-num', text: '#' + (inp.num || inp.id) + ' · ' + (inp.type || '') }));
    meta.appendChild(el('div', { className: 'regie-card-label', text: inp.label || '' }));
    card.appendChild(meta);

    // Controls (annonce + duree)
    var controls = el('div', { className: 'regie-card-ctrl' });
    var sel = buildAnnounceSelect(DEFAULT_ANNOUNCE);
    controls.appendChild(el('label', { className: 'regie-card-announce' }, ['Annonce', sel]));

    var inputDur = el('input', { type: 'number', min: '0', max: '1800', value: String(DEFAULT_DURATION_S) });
    inputDur.dataset.role = 'duration';
    controls.appendChild(el('label', { className: 'regie-card-dur' }, [
      'Durée ', inputDur, ' s (0 = manuel)'
    ]));

    card.appendChild(controls);

    // Bouton diffuser
    var btn = el('button', {
      className: 'regie-card-btn',
      type: 'button',
      onclick: function(ev) { ev.preventDefault(); broadcastInput(card, inp); },
    }, ['▸▸ DIFFUSER']);
    card.appendChild(btn);

    return card;
  }

  function isMsgBoxCollapsed() {
    try { return localStorage.getItem('regie-msg-collapsed') === '1'; }
    catch (e) { return false; }
  }
  function setMsgBoxCollapsed(collapsed) {
    try { localStorage.setItem('regie-msg-collapsed', collapsed ? '1' : '0'); }
    catch (e) {}
  }
  function toggleMessageBox() {
    var box = document.querySelector('.regie-msg-box');
    if (!box) return;
    var nextCollapsed = !box.classList.contains('collapsed');
    box.classList.toggle('collapsed', nextCollapsed);
    setMsgBoxCollapsed(nextCollapsed);
    var chev = box.querySelector('.regie-msg-chevron');
    if (chev) chev.textContent = nextCollapsed ? '▸' : '▾';
  }

  function renderMessageBox() {
    var collapsed = isMsgBoxCollapsed();
    var box = el('div', { className: 'regie-msg-box' + (collapsed ? ' collapsed' : '') });

    // Header cliquable (toggle plier/deplier)
    var header = el('div', {
      className: 'regie-msg-header',
      'data-action': 'toggle-msg-box',
      role: 'button',
      tabindex: '0',
    });
    var chev = el('span', { className: 'regie-msg-chevron', text: collapsed ? '▸' : '▾' });
    header.appendChild(chev);
    header.appendChild(el('h3', { text: 'Message libre' }));
    box.appendChild(header);

    // Wrapper du contenu pliable
    var inner = el('div', { className: 'regie-msg-inner' });
    inner.appendChild(el('p', {
      className: 'regie-msg-hint',
      text: "Diffusion d'un message ad-hoc en plein écran. Idéal pour annoncer un point de situation imprévu."
    }));

    var grid = el('div', { className: 'regie-msg-grid' });

    var title = el('input', {
      type: 'text', maxlength: '120',
      placeholder: 'Titre du message',
      id: 'regie-msg-title',
    });
    grid.appendChild(el('label', {}, ['Titre', title]));

    var body = el('textarea', {
      maxlength: '1500', rows: '4',
      placeholder: 'Corps du message (optionnel, jusqu\'à 1500 caractères)',
      id: 'regie-msg-body',
    });
    grid.appendChild(el('label', {}, ['Corps', body]));

    var level = el('select', { id: 'regie-msg-level' });
    [
      ['info', 'Information (bleu)'],
      ['warning', 'Avertissement (orange)'],
      ['alert', 'Alerte (rouge)'],
      ['critical', 'Critique (rouge clignotant)'],
    ].forEach(function(p) {
      level.appendChild(el('option', { value: p[0], text: p[1] }));
    });
    grid.appendChild(el('label', {}, ['Niveau', level]));

    var optsLine = el('div', { className: 'regie-msg-opts' });
    var msgAnnounceSel = buildAnnounceSelect('alert', 'regie-msg-announce');
    optsLine.appendChild(el('label', {}, ['Annonce', msgAnnounceSel]));

    var durInput = el('input', {
      type: 'number', min: '0', max: '1800', value: '120',
      id: 'regie-msg-duration',
    });
    optsLine.appendChild(el('label', {}, ['Durée ', durInput, ' s (0 = manuel)']));
    grid.appendChild(optsLine);

    var btn = el('button', {
      className: 'regie-msg-btn',
      type: 'button',
      onclick: function(ev) { ev.preventDefault(); broadcastMessage(); },
    }, ['▸▸ DIFFUSER LE MESSAGE']);
    grid.appendChild(btn);

    inner.appendChild(grid);
    box.appendChild(inner);
    return box;
  }

  // ===================================================================
  // Broadcast
  // ===================================================================
  function setCardState(card, state, msg) {
    if (!card) return;
    card.dataset.state = state || '';
    var btn = card.querySelector('.regie-card-btn');
    if (btn) {
      btn.disabled = (state === 'sending');
      if (state === 'sending') btn.textContent = '… Envoi';
      else if (state === 'on-air') btn.textContent = '● À L\'ANTENNE';
      else if (state === 'error') btn.textContent = '⚠ ERREUR ' + (msg ? '· ' + msg : '');
      else btn.textContent = '▸▸ DIFFUSER';
    }
  }

  function broadcastInput(card, inp) {
    var selAnnounce = card.querySelector('select[data-role="announce"]');
    var inDur = card.querySelector('input[data-role="duration"]');
    var announce = (selAnnounce && selAnnounce.value) || 'alert';
    var dur = parseInt((inDur && inDur.value) || '0', 10);
    var duration_s = (isFinite(dur) && dur >= 1 && dur <= 1800) ? dur : null;

    setCardState(card, 'sending');
    var payload = {
      type: 'input',
      input_id: inp.id,
      announce: announce,
      duration_s: duration_s,
    };
    postJson('/state', payload).then(function(res) {
      if (res.status === 200 && res.json && res.json.ok) {
        REGIE_LAST_VERSION = res.json.version;
        REGIE_LAST_PAYLOAD = res.json.payload;
        renderOnAir(res.json.payload);
        // Sync immediate : on-air sur la nouvelle carte, reset des autres.
        syncCardStates(res.json.payload);
      } else {
        var detail = (res.json && res.json.detail) || (res.json && res.json.error) || ('HTTP ' + res.status);
        setCardState(card, 'error', detail);
        setTimeout(function() { setCardState(card, ''); }, 4000);
      }
    }).catch(function(err) {
      setCardState(card, 'error', String(err && err.message || err));
      setTimeout(function() { setCardState(card, ''); }, 4000);
    });
  }

  function broadcastMessage() {
    var title = ($('regie-msg-title').value || '').trim();
    var body = $('regie-msg-body').value || '';
    var level = $('regie-msg-level').value || 'info';
    var announce = ($('regie-msg-announce') && $('regie-msg-announce').value) || 'alert';
    var dur = parseInt($('regie-msg-duration').value || '0', 10);
    var duration_s = (isFinite(dur) && dur >= 1 && dur <= 1800) ? dur : null;

    if (!title) { flashMessageStatus('error', 'Titre requis'); return; }
    if (title.length > 120) { flashMessageStatus('error', 'Titre trop long (max 120)'); return; }
    if (body.length > 1500) { flashMessageStatus('error', 'Corps trop long (max 1500)'); return; }

    flashMessageStatus('sending', 'Envoi en cours…');
    postJson('/state', {
      type: 'message',
      title: title, body: body, level: level,
      announce: announce, duration_s: duration_s,
    }).then(function(res) {
      if (res.status === 200 && res.json && res.json.ok) {
        flashMessageStatus('ok', 'Message diffusé');
        REGIE_LAST_VERSION = res.json.version;
        REGIE_LAST_PAYLOAD = res.json.payload;
        renderOnAir(res.json.payload);
        syncCardStates(res.json.payload);  // reset les cartes input si elles l'etaient
      } else {
        var detail = (res.json && res.json.detail) || (res.json && res.json.error) || ('HTTP ' + res.status);
        flashMessageStatus('error', detail);
      }
    }).catch(function(err) {
      flashMessageStatus('error', String(err && err.message || err));
    });
  }

  function flashMessageStatus(kind, msg) {
    var btn = document.querySelector('.regie-msg-btn');
    if (!btn) return;
    btn.dataset.state = kind || '';
    btn.disabled = (kind === 'sending');
    if (kind === 'sending') btn.textContent = '… ' + msg;
    else if (kind === 'ok') btn.textContent = '✓ ' + msg;
    else if (kind === 'error') btn.textContent = '⚠ ' + msg;
    setTimeout(function() {
      btn.dataset.state = '';
      btn.disabled = false;
      btn.textContent = '▸▸ DIFFUSER LE MESSAGE';
    }, kind === 'error' ? 4000 : 2500);
  }

  function clearBroadcast() {
    postJson('/clear', {}).then(function(res) {
      if (res.status === 200 && res.json && res.json.ok) {
        REGIE_LAST_VERSION = res.json.version;
        REGIE_LAST_PAYLOAD = res.json.payload;
        renderOnAir(res.json.payload);
        syncCardStates(res.json.payload);
      }
    });
  }

  // Synchronise le data-state="on-air" des cartes inputs avec le payload courant.
  // - Si payload.type==="input", la carte avec input_id correspondant est on-air,
  //   les autres sont reset.
  // - Sinon (idle ou message), toutes les cartes sont reset.
  // Appele apres chaque pollStatus pour suivre les auto-clear / changements externes.
  function syncCardStates(payload) {
    var activeId = (payload && payload.type === 'input') ? payload.input_id : null;
    document.querySelectorAll('.regie-card').forEach(function(c) {
      var cardId = parseInt(c.dataset.inputId, 10);
      var current = c.dataset.state || '';
      if (cardId === activeId) {
        if (current !== 'on-air') setCardState(c, 'on-air');
      } else {
        // On ne touche pas aux cartes en cours d'envoi ou en erreur (transient)
        if (current === 'on-air') setCardState(c, '');
      }
    });
  }

  // ===================================================================
  // Panneau "À l'antenne" + statut TV
  // ===================================================================
  function renderOnAir(payload) {
    var box = $('regie-onair');
    if (!box) return;
    clearChildren(box);

    var statusBlock = el('div', { className: 'regie-onair-status' });
    var connected = REGIE_TV_CLIENTS.length;
    var statusKind = connected > 0 ? 'ok' : 'error';
    statusBlock.appendChild(el('span', {
      className: 'regie-tv-pill regie-tv-' + statusKind,
    }, [
      connected > 0 ? '● TV en ligne (' + connected + ')' : '● TV hors ligne',
    ]));
    box.appendChild(statusBlock);

    if (!payload || payload.type === 'idle') {
      box.appendChild(el('div', { className: 'regie-onair-idle', text: 'Aucune diffusion · TV en mode horloge' }));
      return;
    }

    var info = el('div', { className: 'regie-onair-info' });
    var line1 = el('div', { className: 'regie-onair-line1' });
    if (payload.type === 'input') {
      line1.appendChild(el('span', { className: 'regie-onair-tag', text: 'INPUT #' + (payload.num || payload.input_id) }));
      line1.appendChild(el('span', { className: 'regie-onair-label', text: payload.label || '' }));
    } else if (payload.type === 'message') {
      line1.appendChild(el('span', { className: 'regie-onair-tag', text: 'MESSAGE · ' + (payload.level || 'info').toUpperCase() }));
      line1.appendChild(el('span', { className: 'regie-onair-label', text: payload.title || '' }));
    }
    info.appendChild(line1);

    var line2 = el('div', { className: 'regie-onair-line2' });
    if (payload.started_at) {
      var startedMs = Date.parse(payload.started_at);
      if (!isNaN(startedMs)) {
        var ageS = Math.max(0, Math.round((Date.now() - startedMs) / 1000));
        var ageStr = Math.floor(ageS / 60) + ':' + (ageS % 60 < 10 ? '0' : '') + (ageS % 60);
        line2.appendChild(el('span', { text: 'À l\'antenne depuis ' + ageStr }));
      }
    }
    if (typeof payload.duration_s === 'number' && payload.duration_s > 0) {
      line2.appendChild(el('span', { text: ' · auto-clear ' + payload.duration_s + 's' }));
    } else {
      line2.appendChild(el('span', { text: ' · clear manuel' }));
    }
    info.appendChild(line2);
    box.appendChild(info);

    box.appendChild(el('button', {
      className: 'regie-onair-stop',
      type: 'button',
      'data-action': 'stop-broadcast',
    }, ['■ STOPPER LA DIFFUSION']));
  }

  // ===================================================================
  // Polling status (cote regie : 3s)
  // ===================================================================
  function pollStatus() {
    // Pas de ?client= : la regie ne doit pas se compter comme une TV.
    getJson('/state').then(function(res) {
      if (res.status === 200 && res.json && res.json.ok) {
        REGIE_LAST_VERSION = res.json.version;
        REGIE_LAST_PAYLOAD = res.json.payload;
        REGIE_TV_CLIENTS = res.json.tv_clients || [];
        renderOnAir(res.json.payload);
        syncCardStates(res.json.payload);
        updateSidebarBadge();
      }
    }).catch(function() { /* silencieux */ })
      .then(function() { setTimeout(pollStatus, STATUS_POLL_MS); });
  }

  function updateSidebarBadge() {
    var nav = document.querySelector('.nav-item[data-view="regie"] .badge');
    if (!nav) return;
    var n = REGIE_TV_CLIENTS.length;
    if (n > 0) {
      nav.textContent = '●';
      nav.style.background = '#047857';
      nav.style.color = 'white';
    } else {
      nav.textContent = '○';
      nav.style.background = '';
      nav.style.color = '';
    }
  }

  // Re-render periodique du chrono "depuis Xs"
  setInterval(function() {
    if (REGIE_LAST_PAYLOAD && REGIE_LAST_PAYLOAD.type !== 'idle') {
      renderOnAir(REGIE_LAST_PAYLOAD);
    }
  }, 1000);

  // ===================================================================
  // Boot public
  // ===================================================================
  window.initRegie = function() {
    fetchCsrfToken()
      .then(loadManifest)
      .then(function() {
        renderRegieShell();
        pollStatus();
      })
      .catch(function(err) {
        var view = $('view-regie');
        if (view) {
          clearChildren(view);
          view.appendChild(el('div', { className: 'section-header' }, [
            el('h2', { text: 'Live feed régie — erreur' }),
            el('p', { text: "Impossible d'initialiser la régie : " + (err && err.message || err) }),
          ]));
        }
      });
  };
})();
