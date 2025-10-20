// === Trafic sans Leaflet ===
// On ne dessine plus de polylines, on alimente le widget (table) et les compteurs.

let trafficEnabled = false;

// Utils DOM
function $(id){ return document.getElementById(id); }
function on(elOrId, evt, fn){
  const el = typeof elOrId === "string" ? $(elOrId) : elOrId;
  if (el) el.addEventListener(evt, fn, false);
}

// Helpers
function formatTime(seconds) {
    if (seconds == null || isNaN(seconds)) return '—';
    seconds = Math.max(0, seconds|0);
    const m = Math.floor(seconds / 60);
    const s = seconds % 60;
    return `${m}m ${s}s`;
}
function getColor(level) {
    switch (level) {
        case 1: return 'darkgreen';
        case 2: return 'yellow';
        case 3: return 'orange';
        case 4: return 'red';
        case 5: return 'darkred';
        default: return 'green';
    }
}
function calculateDelay(time, historicTime) {
    if (time == null || historicTime == null) return 0;
    return (time|0) - (historicTime|0);
}

// Rendu d’un tableau minimal des routes (si tu veux garder une lecture)
function renderRoutesTable(routes) {
    const box = $('traffic-table-container');
    if (!box) return;

    if (!routes || !routes.length) {
        box.innerHTML = '<div style="opacity:.8;font-size:12px;">Pas de données trafic</div>';
        return;
    }

    const rows = routes.map(r => {
        const delay = calculateDelay(r.time, r.historicTime);
        const color = getColor(r.jamLevel);
        return `
        <tr>
          <td style="padding:6px;">${r.name || '—'}</td>
          <td style="padding:6px;">${formatTime(r.time)} / ${formatTime(r.historicTime)}</td>
          <td style="padding:6px;font-weight:700;color:${color};">${formatTime(delay)}</td>
          <td style="padding:6px;">${r.jamLevel ?? '—'}</td>
        </tr>`;
    }).join('');

    box.innerHTML = `
      <table style="width:100%;border-collapse:collapse;font-size:12px;">
        <thead>
          <tr style="background:#ff007f;color:#fff;">
            <th style="text-align:left;padding:6px;">Itinéraire</th>
            <th style="text-align:left;padding:6px;">Actuel / Moyen</th>
            <th style="text-align:left;padding:6px;">Retard</th>
            <th style="text-align:left;padding:6px;">Niveau</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>`;
}

// Toggle du switch (ne fait qu’afficher/masquer le tableau)
on('trafficToggle', 'change', function (e) {
    trafficEnabled = !!e.target.checked;
    const box = $('traffic-table-container');
    if (box) box.style.display = trafficEnabled ? 'block' : 'none';
    if (trafficEnabled) {
        updateTrafficData();
    }
});

// Récupère les données et alimente le tableau
function updateTrafficData() {
    if (!trafficEnabled) return;
    fetch('/trafic/data')
        .then(r => r.json())
        .then(data => {
            const routes = Array.isArray(data?.routes) ? data.routes.slice() : [];
            // Tri par retard décroissant
            routes.sort((a, b) => calculateDelay(b.time, b.historicTime) - calculateDelay(a.time, a.historicTime));
            renderRoutesTable(routes);
        })
        .catch(err => console.error('Erreur lors de la récupération des données de trafic:', err));
}

// === TEMPS D'ATTENTE PARKINGS/CAMPINGS -> maintenant dans le widget gauche ===
function levelClass(sev){
  switch(sev){
    case 0: return 'level-better';   // plus fluide
    case 1: return 'level-normal';
    case 2: return 'level-busy';
    case 3: return 'level-heavy';
    case 4: return 'level-gridlock';
    default: return 'level-normal';
  }
}
function fmtTime(s){ return `${s|0}s`; }
function fmtDeltaSign(n){ return n > 0 ? `+${n}` : `${n}`; }

// Affiche "+Xm Ys" (ou "+0s" si aucun retard)
function formatDelay(seconds){
  let s = Math.max(0, seconds|0);
  if (s === 0) return '+0s';
  const m = Math.floor(s / 60);
  const r = s % 60;
  if (m && r) return `+${m}m ${r}s`;
  if (m)      return `+${m}m`;
  return `+${r}s`;
}

// --- Regroupe les enregistrements par terrain, avec sous-clés in/out
function groupByTerrain(terrains) {
  const map = new Map();
  for (const it of terrains) {
    const name = it.terrain || '—';
    const dir  = (it.direction === 'out' || it.direction === 'in') ? it.direction : 'in'; // fallback
    let entry = map.get(name);
    if (!entry) {
      entry = { name, in: null, out: null };
      map.set(name, entry);
    }
    // Si plusieurs lignes pour la même direction existent déjà (ex: addition côté backend),
    // on garde la plus pénalisante (max ratio)
    const prev = entry[dir];
    if (!prev || (it.ratio ?? 0) > (prev.ratio ?? 0)) {
      entry[dir] = it;
    }
  }
  return Array.from(map.values());
}

// --- Ratio “efficace” pour trier même si ratio null
function effectiveRatio(r) {
  const cur  = r?.currentTime ?? 0;
  const hist = r?.historicTime ?? 0;
  if (r?.ratio != null) return r.ratio;
  if (hist > 0) return cur / hist;
  // Pas d’historique: approx via la sévérité (0..4) → 1.0..2.2
  const sev = r?.severity ?? 1;
  return 1 + (sev * 0.3);
}

// --- Rend une mini-pastille IN/OUT compacte
function renderDirPill(rec, label) {
  if (!rec) return '';
  const delay = Math.max(0, (rec.currentTime|0) - (rec.historicTime|0));
  const title = `${label} • Actuel: ${formatTime(rec.currentTime)} • Retard: ${formatDelay(delay)}`;
  return `
    <span class="state-pill ${levelClass(rec.severity)}" title="${title}">
      ${label}&nbsp;${formatTime(rec.currentTime)} · ${formatDelay(delay)}
    </span>
  `;
}

// === Mise à jour du widget Accès parkings ===
function updateParkingIndicators() {
  const container = document.getElementById('parking-indicators-list');
  if (!container) return;

  fetch('/trafic/waiting_data_structured')
    .then(r => r.json())
    .then(data => {
      container.innerHTML = "";

      const terrainsRaw = Array.isArray(data?.terrains) ? data.terrains.slice() : [];
      // 1) Regrouper par terrain → { name, in, out }
      const grouped = groupByTerrain(terrainsRaw);

      // 2) Trier par “pire” ratio entre IN/OUT, décroissant
      grouped.sort((A, B) => {
        const aWorst = Math.max(effectiveRatio(A.in), effectiveRatio(A.out));
        const bWorst = Math.max(effectiveRatio(B.in), effectiveRatio(B.out));
        return bWorst - aWorst;
      });

      // 3) Rendu: une ligne par parking, 2 pastilles à droite (si une seule direction, une seule pastille)
      grouped.forEach(g => {
        const div = document.createElement('div');
        div.className = 'parking-row';
        div.innerHTML = `
          <div class="name">${g.name}</div>
          <div class="pill-duo">
            ${renderDirPill(g.in,  'IN')}
            ${renderDirPill(g.out, 'OUT')}
          </div>
        `;
        container.appendChild(div);
      });
    })
    .catch(err => console.error('Erreur trafic (widget accès) :', err));
}

document.addEventListener('DOMContentLoaded', function() {
    // Par défaut, on masque le tableau tant que le switch n’est pas ON
    const box = $('traffic-table-container');
    if (box) box.style.display = 'none';

    updateParkingIndicators();
    setInterval(updateParkingIndicators, 30000);

    // Si le toggle est déjà coché (état mémorisé par le navigateur)
    const toggle = $('trafficToggle');
    if (toggle && toggle.checked) {
        trafficEnabled = true;
        if (box) box.style.display = 'block';
        updateTrafficData();
    }

    // Rafraîchir les routes si activé
    setInterval(() => { if (trafficEnabled) updateTrafficData(); }, 60000);
});
