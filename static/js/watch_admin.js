(function () {
  'use strict';

  var API = '/api/v1/watch/admin';
  var state = { config: null, definitions: [], evenements: [] };

  function csrfToken() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute('content') : '';
  }

  function esc(s) {
    var d = document.createElement('div');
    d.textContent = s == null ? '' : String(s);
    return d.innerHTML;
  }

  function request(method, url, body) {
    var options = {
      method: method,
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() },
      credentials: 'same-origin'
    };
    if (body) { options.body = JSON.stringify(body); }
    return fetch(url, options).then(function (r) {
      return r.json().then(function (j) {
        if (!r.ok || j.ok === false) { throw new Error(j.error || r.status); }
        return j;
      });
    });
  }

  var LEVEL_COLORS = { 1: 'var(--brand)', 2: 'var(--warning)', 3: 'var(--danger)' };

  function renderTokens(tokens) {
    var tbody = document.querySelector('#watch-token-table tbody');
    tbody.innerHTML = tokens.map(function (t) {
      var pillClass = t.revoked ? 'is-revoked' : 'is-active';
      var etat = t.revoked ? 'Revoque' : 'Actif';
      var bouton = t.revoked
        ? '<span style="color:var(--muted); font-size:0.8rem;">&mdash;</span>'
        : '<button type="button" class="btn btn-secondary watch-admin-btn-sm" data-revoke="' +
          esc(t._id) + '">Revoquer</button>';
      return '<tr><td>' + esc(t.label) + '</td><td>' + esc(t.created_at) +
             '</td><td>' + esc(t.last_used_at || '--') + '</td><td>' +
             esc(t.last_ip || '--') + '</td><td class="col-shrink"><span class="watch-admin-pill ' +
             pillClass + '">' + etat + '</span></td><td class="col-shrink">' +
             bouton + '</td></tr>';
    }).join('');
  }

  function renderAlerts() {
    var choisis = {};
    (state.config.alerts || []).forEach(function (a) { choisis[a.slug] = a; });
    var tbody = document.querySelector('#watch-alert-table tbody');
    tbody.innerHTML = state.definitions.map(function (d) {
      var regle = choisis[d.slug];
      var coche = regle ? ' checked' : '';
      var niveau = regle ? regle.level : 1;
      var label = regle && regle.label ? regle.label : d.name;
      var slugAttr = esc(d.slug);
      var niveaux = [1, 2, 3].map(function (n) {
        return '<button type="button" class="watch-admin-level-btn' +
               (n === niveau ? ' selected' : '') + '" data-slug="' + slugAttr +
               '" data-level="' + n + '" style="--lc:' + LEVEL_COLORS[n] + ';">' +
               n + '</button>';
      }).join('');
      return '<tr><td class="col-shrink"><input type="checkbox" data-slug="' + slugAttr + '"' +
             coche + '></td><td>' + esc(d.name) + '</td>' +
             '<td><div class="watch-admin-level-row">' + niveaux + '</div></td>' +
             '<td><input type="text" class="form-input" maxlength="24" data-label="' +
             slugAttr + '" value="' + esc(label) + '"></td></tr>';
    }).join('');
  }

  function renderEvent() {
    var select = document.getElementById('watch-event-select');
    select.innerHTML = state.evenements.map(function (e) {
      return '<option value="' + esc(e.nom) + '"' +
             (e.nom === state.config.event ? ' selected' : '') + '>' +
             esc(e.nom) + '</option>';
    }).join('');
    document.getElementById('watch-year').value = state.config.year || '';
    var mode = state.config.event_mode || 'auto';
    document.querySelectorAll('input[name="watch-mode"]').forEach(function (r) {
      r.checked = (r.value === mode);
    });
    toggleEventInputs(mode);

    var seuils = state.config.wbgt_levels || [25, 28, 30];
    document.getElementById('watch-wbgt-1').value = seuils[0];
    document.getElementById('watch-wbgt-2').value = seuils[1];
    document.getElementById('watch-wbgt-3').value = seuils[2];
  }

  function toggleEventInputs(mode) {
    var epingle = (mode === 'pinned');
    document.getElementById('watch-event-select').disabled = !epingle;
    document.getElementById('watch-year').disabled = !epingle;
  }

  function collect() {
    var alerts = [];
    document.querySelectorAll('#watch-alert-table tbody input[type="checkbox"][data-slug]')
      .forEach(function (cb) {
        if (!cb.checked) { return; }
        var slug = cb.getAttribute('data-slug');
        var selectedBtn = document.querySelector(
          '.watch-admin-level-btn.selected[data-slug="' + slug + '"]');
        alerts.push({
          slug: slug,
          level: selectedBtn ? parseInt(selectedBtn.getAttribute('data-level'), 10) : 1,
          label: document.querySelector('[data-label="' + slug + '"]').value
        });
      });
    var mode = document.querySelector('input[name="watch-mode"]:checked').value;
    return {
      event_mode: mode,
      event: mode === 'pinned'
        ? document.getElementById('watch-event-select').value : null,
      year: mode === 'pinned'
        ? parseInt(document.getElementById('watch-year').value, 10) : null,
      alerts: alerts,
      wbgt_levels: [
        parseFloat(document.getElementById('watch-wbgt-1').value),
        parseFloat(document.getElementById('watch-wbgt-2').value),
        parseFloat(document.getElementById('watch-wbgt-3').value)
      ]
    };
  }

  function load() {
    request('GET', API + '/config').then(function (j) {
      state.config = j.config;
      state.definitions = j.definitions;
      state.evenements = j.evenements;
      renderEvent();
      renderAlerts();
    }).catch(function (e) { showToast('error', 'Chargement impossible : ' + e.message); });

    request('GET', API + '/tokens').then(function (j) {
      renderTokens(j.tokens);
    }).catch(function (e) { showToast('error', 'Jetons illisibles : ' + e.message); });
  }

  document.addEventListener('DOMContentLoaded', function () {
    load();

    document.getElementById('watch-token-form')
      .addEventListener('submit', function (ev) {
        ev.preventDefault();
        var label = document.getElementById('watch-token-label').value;
        request('POST', API + '/tokens', { label: label }).then(function (j) {
          var box = document.getElementById('watch-token-clear');
          box.hidden = false;
          document.getElementById('watch-token-clear-value').textContent = j.token;
          document.getElementById('watch-token-label').value = '';
          showToast('success', 'Jeton emis.');
          load();
        }).catch(function (e) { showToast('error', 'Emission refusee : ' + e.message); });
      });

    document.getElementById('watch-token-copy')
      .addEventListener('click', function () {
        var value = document.getElementById('watch-token-clear-value').textContent;
        if (!value) { return; }
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(value).then(function () {
            showToast('success', 'Jeton copie dans le presse-papiers.');
          }).catch(function () {
            showToast('error', 'Copie impossible. Selectionnez et copiez manuellement.');
          });
        } else {
          showToast('error', 'Copie non supportee par ce navigateur. Selectionnez et copiez manuellement.');
        }
      });

    document.querySelector('#watch-token-table')
      .addEventListener('click', function (ev) {
        var id = ev.target.getAttribute && ev.target.getAttribute('data-revoke');
        if (!id) { return; }
        request('POST', API + '/tokens/' + id + '/revoke').then(function () {
          showToast('success', 'Jeton revoque.');
          load();
        }).catch(function (e) { showToast('error', 'Revocation refusee : ' + e.message); });
      });

    document.querySelector('#watch-alert-table')
      .addEventListener('click', function (ev) {
        var btn = ev.target.closest && ev.target.closest('.watch-admin-level-btn');
        if (!btn) { return; }
        var slug = btn.getAttribute('data-slug');
        document.querySelectorAll('.watch-admin-level-btn[data-slug="' + slug + '"]')
          .forEach(function (b) { b.classList.remove('selected'); });
        btn.classList.add('selected');
      });

    document.querySelectorAll('input[name="watch-mode"]').forEach(function (r) {
      r.addEventListener('change', function () { toggleEventInputs(r.value); });
    });

    document.getElementById('watch-save').addEventListener('click', function () {
      request('PUT', API + '/config', collect()).then(function () {
        showToast('success', 'Configuration enregistree.');
      }).catch(function (e) { showToast('error', 'Enregistrement refuse : ' + e.message); });
    });
  });
}());
