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

  function say(message, erreur) {
    var el = document.getElementById('watch-status');
    el.textContent = message;
    el.className = erreur ? 'error' : 'ok';
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

  function renderTokens(tokens) {
    var tbody = document.querySelector('#watch-token-table tbody');
    tbody.innerHTML = tokens.map(function (t) {
      var etat = t.revoked ? 'revoque' : 'actif';
      var bouton = t.revoked ? ''
        : '<button data-revoke="' + esc(t._id) + '">Revoquer</button>';
      return '<tr><td>' + esc(t.label) + '</td><td>' + esc(t.created_at) +
             '</td><td>' + esc(t.last_used_at || '--') + '</td><td>' +
             esc(t.last_ip || '--') + '</td><td>' + etat + '</td><td>' +
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
      return '<tr><td><input type="checkbox" data-slug="' + esc(d.slug) + '"' +
             coche + '></td><td>' + esc(d.name) + '</td>' +
             '<td><select data-level="' + esc(d.slug) + '">' +
             [1, 2, 3].map(function (n) {
               return '<option value="' + n + '"' +
                      (n === niveau ? ' selected' : '') + '>' + n + '</option>';
             }).join('') + '</select></td>' +
             '<td><input type="text" maxlength="24" data-label="' +
             esc(d.slug) + '" value="' + esc(label) + '"></td></tr>';
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
    document.querySelectorAll('[data-slug]').forEach(function (cb) {
      if (!cb.checked) { return; }
      var slug = cb.getAttribute('data-slug');
      alerts.push({
        slug: slug,
        level: parseInt(
          document.querySelector('[data-level="' + slug + '"]').value, 10),
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
    }).catch(function (e) { say('Chargement impossible : ' + e.message, true); });

    request('GET', API + '/tokens').then(function (j) {
      renderTokens(j.tokens);
    }).catch(function (e) { say('Jetons illisibles : ' + e.message, true); });
  }

  document.addEventListener('DOMContentLoaded', function () {
    load();

    document.getElementById('watch-token-form')
      .addEventListener('submit', function (ev) {
        ev.preventDefault();
        var label = document.getElementById('watch-token-label').value;
        request('POST', API + '/tokens', { label: label }).then(function (j) {
          var p = document.getElementById('watch-token-clear');
          p.hidden = false;
          p.textContent = 'Jeton (affiche une seule fois) : ' + j.token;
          document.getElementById('watch-token-label').value = '';
          load();
        }).catch(function (e) { say('Emission refusee : ' + e.message, true); });
      });

    document.querySelector('#watch-token-table')
      .addEventListener('click', function (ev) {
        var id = ev.target.getAttribute && ev.target.getAttribute('data-revoke');
        if (!id) { return; }
        request('POST', API + '/tokens/' + id + '/revoke').then(function () {
          say('Jeton revoque.');
          load();
        }).catch(function (e) { say('Revocation refusee : ' + e.message, true); });
      });

    document.querySelectorAll('input[name="watch-mode"]').forEach(function (r) {
      r.addEventListener('change', function () { toggleEventInputs(r.value); });
    });

    document.getElementById('watch-save').addEventListener('click', function () {
      request('PUT', API + '/config', collect()).then(function () {
        say('Configuration enregistree.');
      }).catch(function (e) { say('Enregistrement refuse : ' + e.message, true); });
    });
  });
}());
