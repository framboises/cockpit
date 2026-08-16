(function () {
  'use strict';

  var API = '/api/v1/watch/admin';
  var state = { config: null, definitions: [], evenements: [], tokens: [], guidage: {} };

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
    state.tokens = tokens;
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

  // --- Guidage ---------------------------------------------------------
  //
  // L'operateur clique un point sur la carte, le nomme, choisit une montre.
  // Le point remplace celui de cette montre : c'est une destination unique
  // (<< ou dois-je aller MAINTENANT >>), pas une file d'attente.

  var CIRCUIT_BBOX = [[47.9295, 0.1930], [47.9720, 0.2620]];

  var guidage = { map: null, marker: null, latlng: null };

  function initGuidageMap() {
    var div = document.getElementById('watch-guidage-map');
    if (!div || guidage.map || typeof L === 'undefined') { return; }

    guidage.map = L.map(div, { zoomControl: true });
    var osm = L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxNativeZoom: 19, maxZoom: 22, attribution: '&copy; OpenStreetMap'
    }).addTo(guidage.map);
    var aco = L.tileLayer('/tiles/{z}/{x}/{y}.png', {
      tms: true, maxZoom: 22, attribution: 'ACO'
    });
    var ign = L.tileLayer('https://data.geopf.fr/wmts?SERVICE=WMTS&VERSION=1.0.0&REQUEST=GetTile&LAYER=ORTHOIMAGERY.ORTHOPHOTOS&STYLE=normal&TILEMATRIXSET=PM&FORMAT=image/jpeg&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}', {
      maxNativeZoom: 19, maxZoom: 22, attribution: 'IGN-F/Geoplateforme'
    });
    L.control.layers({ 'OSM': osm, 'Satellite ACO': aco, 'Satellite IGN': ign },
                     null, { position: 'topright' }).addTo(guidage.map);
    guidage.map.fitBounds(CIRCUIT_BBOX);

    // Calque des guidages DEJA en cours : sans lui, l'operateur ecraserait
    // un guidage sans savoir qu'il en existait un.
    guidage.layerActifs = L.layerGroup().addTo(guidage.map);

    guidage.map.on('click', function (ev) { poserPoint(ev.latlng); });

    // La carte est construite dans un conteneur dont la taille n'est connue
    // qu'apres la mise en page : sans ce recalcul, les tuiles se dessinent
    // sur une portion de la zone seulement.
    setTimeout(function () { if (guidage.map) { guidage.map.invalidateSize(); } }, 120);
  }

  function poserPoint(latlng) {
    guidage.latlng = latlng;
    if (guidage.marker) {
      guidage.marker.setLatLng(latlng);
    } else {
      guidage.marker = L.marker(latlng, { draggable: true }).addTo(guidage.map);
      guidage.marker.on('dragend', function () {
        guidage.latlng = guidage.marker.getLatLng();
        majCoords();
      });
    }
    majCoords();
  }

  function majCoords() {
    var div = document.getElementById('watch-guidage-coords');
    var bouton = document.getElementById('watch-guidage-send');
    if (!guidage.latlng) {
      div.textContent = 'Aucun point selectionne.';
      div.classList.remove('watch-guidage-coords-set');
      if (bouton) { bouton.disabled = true; }
      return;
    }
    div.textContent = 'Point : ' + guidage.latlng.lat.toFixed(6) + ', '
                      + guidage.latlng.lng.toFixed(6)
                      + ' — faites glisser le marqueur pour ajuster.';
    div.classList.add('watch-guidage-coords-set');
    if (bouton) { bouton.disabled = false; }
  }

  function renderGuidageTokens() {
    var select = document.getElementById('watch-guidage-token');
    if (!select) { return; }
    var precedent = select.value;
    // Une montre revoquee ne lira jamais le point : la proposer ferait
    // croire a un envoi reussi.
    var actives = (state.tokens || []).filter(function (t) { return !t.revoked; });
    select.innerHTML = actives.map(function (t) {
      return '<option value="' + esc(t._id) + '">' + esc(t.label || t._id) + '</option>';
    }).join('');
    if (precedent) { select.value = precedent; }
    var vide = actives.length === 0;
    select.disabled = vide;
    if (vide) {
      select.innerHTML = '<option value="">Aucune montre active</option>';
    }
  }

  function nomMontre(id) {
    var trouve = (state.tokens || []).filter(function (t) { return t._id === id; })[0];
    return trouve ? (trouve.label || id) : id;
  }

  function renderGuidageTable() {
    var tbody = document.querySelector('#watch-guidage-table tbody');
    if (!tbody) { return; }
    var ids = Object.keys(state.guidage || {});
    if (!ids.length) {
      tbody.innerHTML = '<tr><td colspan="5" style="color:var(--muted);">'
                        + 'Aucun guidage en cours.</td></tr>';
    } else {
      tbody.innerHTML = ids.map(function (id) {
        var g = state.guidage[id];
        return '<tr>'
          + '<td>' + esc(nomMontre(id)) + '</td>'
          + '<td>' + esc(g.label) + '</td>'
          + '<td>' + esc(Number(g.lat).toFixed(5) + ', ' + Number(g.lon).toFixed(5)) + '</td>'
          + '<td>' + esc(formatInstant(g.sent_at)) + '</td>'
          + '<td class="col-shrink">'
          + '<button type="button" class="btn btn-secondary watch-admin-btn-sm" '
          + 'data-guidage-clear="' + esc(id) + '">Effacer</button></td>'
          + '</tr>';
      }).join('');
    }
    majMarqueursActifs();
  }

  function majMarqueursActifs() {
    if (!guidage.layerActifs) { return; }
    guidage.layerActifs.clearLayers();
    Object.keys(state.guidage || {}).forEach(function (id) {
      var g = state.guidage[id];
      if (g.lat == null || g.lon == null) { return; }
      L.circleMarker([g.lat, g.lon], {
        radius: 8, color: '#2563eb', weight: 2, fillOpacity: 0.25
      }).bindTooltip(nomMontre(id) + ' — ' + (g.label || ''),
                     { direction: 'top' }).addTo(guidage.layerActifs);
    });
  }

  function formatInstant(epochSec) {
    if (!epochSec) { return '--'; }
    var d = new Date(epochSec * 1000);
    return d.toLocaleString('fr-FR', { dateStyle: 'short', timeStyle: 'short' });
  }

  function rechargerGuidage() {
    return request('GET', API + '/guidage').then(function (j) {
      state.guidage = j.guidage || {};
      renderGuidageTable();
    });
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
      // Le selecteur de montre et le tableau des guidages dependent tous
      // deux de la liste des jetons : les charger a la suite evite un
      // selecteur vide le temps d'un aller-retour.
      return request('GET', API + '/guidage');
    }).then(function (j) {
      state.guidage = j.guidage || {};
      renderGuidageTokens();
      renderGuidageTable();
    }).catch(function (e) { showToast('error', 'Jetons illisibles : ' + e.message); });
  }

  document.addEventListener('DOMContentLoaded', function () {
    load();
    initGuidageMap();

    document.getElementById('watch-guidage-form')
      .addEventListener('submit', function (ev) {
        ev.preventDefault();
        if (!guidage.latlng) {
          showToast('error', 'Cliquez d\'abord un point sur la carte.');
          return;
        }
        var tokenId = document.getElementById('watch-guidage-token').value;
        if (!tokenId) {
          showToast('error', 'Aucune montre selectionnee.');
          return;
        }
        var label = document.getElementById('watch-guidage-label').value.trim();
        var bouton = document.getElementById('watch-guidage-send');
        // Un double clic enverrait deux fois le point et ferait vibrer la
        // montre deux fois pour un seul geste.
        bouton.disabled = true;
        request('POST', API + '/guidage', {
          token_id: tokenId,
          lat: guidage.latlng.lat,
          lon: guidage.latlng.lng,
          label: label
        }).then(function () {
          showToast('success', 'Point envoye a ' + nomMontre(tokenId)
                    + '. Il arrive au prochain releve de la montre.');
          return rechargerGuidage();
        }).catch(function (e) {
          showToast('error', 'Envoi refuse : ' + e.message);
        }).then(function () { bouton.disabled = false; });
      });

    document.querySelector('#watch-guidage-table')
      .addEventListener('click', function (ev) {
        var id = ev.target.getAttribute
                 && ev.target.getAttribute('data-guidage-clear');
        if (!id) { return; }
        request('DELETE', API + '/guidage/' + id).then(function () {
          showToast('success', 'Guidage efface.');
          return rechargerGuidage();
        }).catch(function (e) {
          showToast('error', 'Effacement refuse : ' + e.message);
        });
      });

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
