/////////////////////////////////////////////////////////////////////////////////////////////////////
// HELPERS
/////////////////////////////////////////////////////////////////////////////////////////////////////

// Fonction utilitaire pour convertir l'heure "HH:MM" en minutes depuis minuit
function timeToMinutes(timeStr) {
    if (!timeStr || timeStr.toUpperCase() === "TBC") return Infinity;
    const parts = timeStr.split(':');
    return parseInt(parts[0], 10) * 60 + parseInt(parts[1], 10);
}

// Fonction pour tronquer une chaîne à un nombre max de caractères
function truncateText(text, maxChars) {
    return text.length > maxChars ? text.substring(0, maxChars) + "…" : text;
}

// 🔹 Construit un index { 'YYYY-MM-DD': { is24h, openTime, closeTime } }
function buildPublicDatesMap(parametrage) {
  const map = {};
  const dates = parametrage?.globalHoraires?.dates || parametrage?.data?.globalHoraires?.dates || [];
  dates.forEach(d => {
    if (d?.date) {
      map[d.date] = {
        is24h: !!d.is24h || (d.openTime === "00:00" && (d.closeTime === "23:59" || d.closeTime === "24:00")),
        openTime: d.openTime || "00:00",
        closeTime: d.closeTime || "23:59"
      };
    }
  });
  return map;
}

// 🔹 Retourne {text, className} pour une date donnée (YYYY-MM-DD)
function getPublicBannerForDateStr(dateStr) {
  const entry = window.publicDatesMap?.[dateStr];
  if (!entry) {
    return { text: "FERMÉ AU PUBLIC", className: "banner-closed" };
  }
  if (entry.is24h) {
    return { text: "OUVERT AU PUBLIC — 24/24", className: "banner-open" };
  }
  return {
    text: `OUVERT AU PUBLIC — ${entry.openTime} – ${entry.closeTime}`,
    className: "banner-open"
  };
}

// --- Helpers TODO robustes ---
function splitTodo(raw) {
  if (raw == null) return [];
  // Si c'est déjà un tableau (strings ou objets), on normalise
  if (Array.isArray(raw)) {
    return raw
      .map(v => (typeof v === 'string' ? v : (v?.text ?? String(v))).trim())
      .filter(Boolean)
      .map(line => {
        const m = line.match(/^-?\s*\[(x|X|\s)?\]\s*(.*)$/);
        if (m) return { text: m[2].trim(), done: !!m[1] && m[1].toLowerCase() === 'x' };
        const done = /^[✓✔]/.test(line);
        const clean = line.replace(/^[✓✔]\s*/, '');
        return { text: clean, done };
      });
  }
  // Sinon on convertit en string en dernier recours
  const str = String(raw);
  if (!str.trim()) return [];
  return str
    .split(/\r?\n|,/)
    .map(s => s.trim())
    .filter(Boolean)
    .map(line => {
      const m = line.match(/^-?\s*\[(x|X|\s)?\]\s*(.*)$/);
      if (m) return { text: m[2].trim(), done: !!m[1] && m[1].toLowerCase() === 'x' };
      const done = /^[✓✔]/.test(line);
      const clean = line.replace(/^[✓✔]\s*/, '');
      return { text: clean, done };
    });
}

function serializeTodo(items) {
  // Tolère un tableau mixte [{text,done}] | ['[x] foo'|'bar']
  return (items || [])
    .map(it => {
      if (typeof it === 'string') {
        const m = it.match(/^-?\s*\[(x|X|\s)?\]\s*(.*)$/);
        if (m) return `- [${m[1] ? 'x' : ' '}] ${m[2].trim()}`;
        return `- [ ] ${it.trim()}`;
      }
      const done = !!it.done;
      const text = (it.text ?? '').trim();
      return `- [${done ? 'x' : ' '}] ${text}`;
    })
    .join('\n');
}

function renderTodoSticky(item) {
  const tasks = splitTodo(item.todo || "");
  if (tasks.length === 0) return "";
  const lis = tasks.map((t, idx) => `
    <li data-idx="${idx}">
      <input type="checkbox" class="todo-checkbox" ${t.done ? 'checked' : ''} />
      <span class="todo-text ${t.done ? 'todo-done' : ''}">${t.text}</span>
    </li>`).join('');
  return `
    <div class="todo-sticky" data-event-id="${item._id}">
      <h6><span class="material-icons" style="font-size:16px;line-height:0;">checklist</span> Préparation</h6>
      <ul>${lis}</ul>
    </div>`;
}

// --- Helpers clustering ---
function norm(s){ return (s||'').toString().toLowerCase().normalize('NFD').replace(/\p{Diacritic}/gu,''); }
function isParkingItem(it) {
  const c = norm(it.category), a = norm(it.activity), p = norm(it.place);
  return /parking/.test(c) || /parking/.test(a) || /parking/.test(p);
}
function isAccueilItem(it) {
  const c = norm(it.category), a = norm(it.activity), p = norm(it.place);
  // "aire d'accueil", "aires accueil", "accueil camping", etc.
  return /aire.*accueil|accueil.*aire|camping|caravane|camp-car|camping-car/.test(c+a+p);
}

const CLUSTER_CONFIG = {
  parking: { label: 'Parkings',        match: isParkingItem,  icon: 'local_parking' },
  accueil: { label: "Aires d'accueil", match: isAccueilItem,  icon: 'rv_hookup' },
  portes:  { label: 'Portes',          match: isDoorItem,     icon: 'meeting_room' } // ou 'door_front'
};

function groupByClusters(items) {
  const rest = [];
  const buckets = {}; // key: `${type}|${kind}|${timeKey}` -> []

  function detectType(it) {
    if (CLUSTER_CONFIG.parking.match(it)) return 'parking';
    if (CLUSTER_CONFIG.accueil.match(it)) return 'accueil';
    if (CLUSTER_CONFIG.portes.match(it))  return 'portes';
    return null;
  }

  items.forEach(it => {
    const type = detectType(it);
    if (!type) { rest.push(it); return; }

    const kind = getOpenCloseKind(it); // 'open'|'close'|null
    if (!kind) { // si on ne sait pas si c’est une ouverture ou une fermeture, on n’agrège pas
      rest.push(it);
      return;
    }

    const timeKey = getClusterTimeKey(it, kind); // "HH:MM" ou "TBC"
    const key = `${type}|${kind}|${timeKey}`;
    (buckets[key] ||= []).push(it);
  });

  const clusters = [];
  Object.entries(buckets).forEach(([key, arr]) => {
    const [type, kind, time] = key.split('|');
    if (arr.length > 1) {
      clusters.push({ type, kind, time, items: arr });
    } else {
      // si un seul item, ne crée pas de cluster, on laisse la carte individuelle
      rest.push(arr[0]);
    }
  });

  return { clusters, rest };
}

// résumé horaire pour un cluster: min(start) – max(end) si dispo
function clusterTimeWindow(items){
  const toMin = s => (s && s.toUpperCase() !== 'TBC') ? timeToMinutes(s) : Infinity;
  const toMax = s => (s && s.toUpperCase() !== 'TBC') ? timeToMinutes(s) : -Infinity;
  const minStart = Math.min(...items.map(it => toMin(it.start)));
  const maxEnd   = Math.max(...items.map(it => toMax(it.end)));
  const m2h = m => `${String(Math.floor(m/60)).padStart(2,'0')}:${String(m%60).padStart(2,'0')}`;

  let txt = 'TBC';
  if (isFinite(minStart) && maxEnd !== -Infinity) txt = `${m2h(minStart)} - ${m2h(maxEnd)}`;
  else if (isFinite(minStart)) txt = m2h(minStart);
  else if (maxEnd !== -Infinity) txt = m2h(maxEnd);
  return txt;
}

function isDoorItem(it) {
  const s = norm(`${it.category||''} ${it.activity||''} ${it.place||''}`);
  // tolérant : porte/portes, gate, portail, entrée/entree
  return /\bporte?s?\b|\bgate\b|portail|entr[ée]e/.test(s);
}

function validTimeStr(s){ return !!(s && s.trim() && s.toUpperCase() !== 'TBC'); }

function getOpenCloseKind(it){
  const s = norm(`${it.activity||''} ${it.category||''} ${it.place||''}`);
  // mots-clés tolérants
  if (/\bouverture\b|ouvre(r|t)?|opening|\bopen\b/.test(s))   return 'open';
  if (/\bfermeture\b|ferme(r|t)?|closing|\bclose(d)?\b/.test(s)) return 'close';
  return null; // on ne force pas si on n'est pas sûr
}

// Retourne l'heure "clé" du regroupement:
function getClusterTimeKey(it, kind){
  if (kind === 'close') {
    if (validTimeStr(it.end))   return it.end;
    if (validTimeStr(it.start)) return it.start;
  } else { // 'open' par défaut
    if (validTimeStr(it.start)) return it.start;
    if (validTimeStr(it.end))   return it.end;
  }
  return 'TBC';
}

/**
 * Supprime les paires ouverture/fermeture à la même heure pour un même type+lieu.
 * - onlyMidnight=true => on ne cible que "00:00" (cas 24/24 oublié)
 */
function removeRedundantOpenClosePairs(byDate, { onlyMidnight = true } = {}) {
  if (!byDate || typeof byDate !== 'object') return;

  const dates = Object.keys(byDate).sort(); // YYYY-MM-DD

  const normPlace = s => norm(s || '').replace(/\s+/g, ' ').trim();
  const detectType = it => {
    if (CLUSTER_CONFIG.parking.match(it)) return 'parking';
    if (CLUSTER_CONFIG.accueil.match(it)) return 'accueil';
    if (CLUSTER_CONFIG.portes.match(it))  return 'portes';
    return null;
  };

  // 1) SAME-DAY: pour chaque date, si on trouve open & close à la même heure → supprimer les deux
  const toDelete = {}; // date -> Set(_id)
  const wantThisTime = (timeStr) => onlyMidnight ? timeStr === '00:00' : !!timeStr && timeStr !== 'TBC';

  dates.forEach(date => {
    const arr = byDate[date] || [];
    const bucket = {}; // key: type|place|time -> {open:[], close:[]}

    arr.forEach(it => {
      const type = detectType(it);
      if (!type) return;
      const kind = getOpenCloseKind(it);        // 'open' | 'close' | null
      if (!kind) return;

      const timeKey = getClusterTimeKey(it, kind); // 'HH:MM' ou 'TBC'
      if (!wantThisTime(timeKey)) return;          // filtre 00:00 par défaut

      const placeKey = normPlace(it.place || '');
      const key = `${type}|${placeKey}|${timeKey}`;
      (bucket[key] ||= { open: [], close: [] })[kind].push(it);
    });

    Object.values(bucket).forEach(group => {
      if (group.open.length && group.close.length) {
        // on supprime toutes les cartes concernées des deux côtés
        group.open.concat(group.close).forEach(it => {
          if (!it || !it._id) return;
          (toDelete[date] ||= new Set()).add(String(it._id));
        });
      }
    });
  });

  // 2) CROSS-DAY (minuit croisé): fermeture 00:00 à J ET ouverture 00:00 à J+1 (même type+lieu) → supprimer les deux
  // Seulement utile si on cible minuit
  if (onlyMidnight) {
    for (let i = 0; i < dates.length - 1; i++) {
      const d0 = dates[i], d1 = dates[i + 1];
      const a0 = (byDate[d0] || []).filter(it => getOpenCloseKind(it) === 'close' && getClusterTimeKey(it, 'close') === '00:00');
      const a1 = (byDate[d1] || []).filter(it => getOpenCloseKind(it) === 'open'  && getClusterTimeKey(it, 'open')  === '00:00');

      if (!a0.length || !a1.length) continue;

      // index d1 (open) par type+place
      const mapOpen = new Map();
      a1.forEach(it => {
        const type = detectType(it);
        if (!type) return;
        const key = `${type}|${normPlace(it.place||'')}`;
        (mapOpen.get(key) || mapOpen.set(key, [])).push(it);
      });

      // pour chaque close(d0) 00:00, cherche open(d1) 00:00 sur même type+lieu
      a0.forEach(itClose => {
        const type = detectType(itClose);
        if (!type) return;
        const key = `${type}|${normPlace(itClose.place||'')}`;
        const matches = mapOpen.get(key);
        if (matches && matches.length) {
          // supprime itClose et toutes les ouvertures d1 correspondantes
          if (itClose._id) (toDelete[d0] ||= new Set()).add(String(itClose._id));
          matches.forEach(itOpen => {
            if (itOpen._id) (toDelete[d1] ||= new Set()).add(String(itOpen._id));
          });
        }
      });
    }
  }

  // 3) Appliquer la suppression
  dates.forEach(date => {
    const del = toDelete[date];
    if (!del || !del.size) return;
    byDate[date] = (byDate[date] || []).filter(it => !del.has(String(it._id)));
  });
}

// Heures invalides -> Infinity (en fin)
function isValidHHMM(s){ return !!(s && s.trim() && s.toUpperCase() !== 'TBC'); }

// minute "primaire" par item, en respectant open/close quand on peut
function getItemSortMinute(it){
  const kind = getOpenCloseKind(it); // 'open'|'close'|null
  if (kind === 'open') {
    if (isValidHHMM(it.start)) return timeToMinutes(it.start);
    if (isValidHHMM(it.end))   return timeToMinutes(it.end);
    return Infinity;
  }
  if (kind === 'close') {
    if (isValidHHMM(it.end))   return timeToMinutes(it.end);
    if (isValidHHMM(it.start)) return timeToMinutes(it.start);
    return Infinity;
  }
  // si on ne sait pas: start puis end
  if (isValidHHMM(it.start)) return timeToMinutes(it.start);
  if (isValidHHMM(it.end))   return timeToMinutes(it.end);
  return Infinity;
}

// minute de tri pour un cluster
function getClusterSortMinute(cluster){
  if (cluster.time && cluster.time !== 'TBC') {
    return timeToMinutes(cluster.time);
  }
  // sinon, on prend le min des minutes des items qu'il contient
  const mins = cluster.items.map(getItemSortMinute).filter(m => Number.isFinite(m));
  return mins.length ? Math.min(...mins) : Infinity;
}

// tie-breakers de tri (même minute)
function labelForItem(it){
  const title = (it.activity||'').split('/')[0].trim();
  const place = (it.place||'').split('/')[0].trim();
  return `${title} ${place}`.trim().toLowerCase();
}

// Renvoie 'ready' | 'progress' | 'none' | null (null => pas d'affichage)
function getPrepStatus(item) {
  const raw = (item.preparation_checked ?? "").toString().toLowerCase().trim();
  if (raw === "true" || raw === "ready" || raw === "ok") return "ready";
  if (raw === "progress" || raw === "inprogress")        return "progress";
  if (raw === "false" || raw === "no" || raw === "non" || raw === "pending") return "none";

  // 🔁 Fallback: déduire depuis le TODO si présent
  const tasks = splitTodo(item.todo || "");
  if (tasks.length === 0) return null;              // pas de statut affiché
  const done = tasks.filter(t => t.done).length;
  if (done === 0) return "none";
  if (done === tasks.length) return "ready";
  return "progress";
}

function getPrepLabel(status) {
  return status === "ready"    ? "Prête"
       : status === "progress" ? "En cours"
       : status === "none"     ? "Non"
       : "";
}

// Statut agrégé d’un cluster (affiche le "pire" rencontré)
function getClusterPrepStatus(cluster) {
  const list = cluster.items.map(getPrepStatus).filter(Boolean);
  if (!list.length) return null;
  if (list.includes("none")) return "none";
  if (list.includes("progress")) return "progress";
  return "ready";
}

/////////////////////////////////////////////////////////////////////////////////////////////////////
// AFFICHAGE
/////////////////////////////////////////////////////////////////////////////////////////////////////

// Fonction pour créer une vignette d'événement dans la timeline avec affichage en deux colonnes
function createEventItem(date, item) {
    const eventItem = document.createElement("div");
    eventItem.classList.add("event-item");

    // Définir une icône selon la catégorie
    let iconHtml = "";
    if (item.category.indexOf("Motos") !== -1) {
        iconHtml = `<span class="material-icons">motorcycle</span>`;
    } else if (item.category.indexOf("Evénement") !== -1) {
        iconHtml = `<span class="material-icons">event</span>`;
    } else {
        iconHtml = `<span class="material-icons">info</span>`;
    }

    const fullTitle = (item.activity || '').split('/')[0].trim();
    const fullPlace = (item.place || '').split('/')[0].trim();
    const truncatedTitle = truncateText(fullTitle || 'Sans titre', 50);

    // Gestion de l'affichage des heures
    let timeInfo = "";
    if (item.start && item.start.trim() !== "" && item.start.toUpperCase() !== "TBC") {
        timeInfo = item.start;
        if (item.end && item.end.trim() !== "" && item.end.toUpperCase() !== "TBC") {
            timeInfo += " - " + item.end;
        }
    } else if (item.end && item.end.trim() !== "" && item.end.toUpperCase() !== "TBC") {
        timeInfo = item.end;
    } else {
        timeInfo = "TBC";
    }

    const prepStatus = getPrepStatus(item);
    const prepHtml = prepStatus
    ? `<span class="prep-chip prep-${prepStatus}" title="Préparation : ${getPrepLabel(prepStatus)}">${getPrepLabel(prepStatus)}</span>`
    : "";

    // Construction du résumé en deux colonnes
    eventItem.innerHTML = `
        <div class="event-summary">
            <div class="event-title">
                ${iconHtml}
                <h5>${truncatedTitle}</h5>
            </div>
            <div class="event-time">
                <p class="time-info">${timeInfo}</p>
                <p class="event-location">${fullPlace}</p>
                ${prepHtml}
            </div>
            <div class="buttons-container">
                <button class="expand-btn">
                    <span class="material-icons">expand_more</span>
                </button>
            </div>
        </div>
        <div class="toggle-content">
            <!-- Version détaillée -->
            <p><strong>Titre complet :</strong> ${fullTitle}</p>
            <p><strong>Heure de début :</strong> ${item.start || "TBC"}</p>
            <p><strong>Heure de fin :</strong> ${item.end || "TBC"}</p>
            <p><strong>Durée :</strong> ${item.duration}</p>
            <p><strong>Département :</strong> ${item.department}</p>
            <p><strong>Lieu détaillé :</strong> ${item.place ? item.place : "Non spécifié"}</p>
            <p><strong>Commentaires :</strong> ${item.remark ? item.remark : "Non spécifié"}</p>
            ${renderTodoSticky(item)}
        </div>
    `;

    const sticky = eventItem.querySelector('.todo-sticky');
    if (sticky) {
    sticky.addEventListener('change', (e) => {
        const input = e.target;
        if (!input.classList.contains('todo-checkbox')) return;

        const li = input.closest('li');
        if (!li) return;

        const idx = Number(li.dataset.idx);
        const tasks = splitTodo(item.todo || "");
        if (Number.isFinite(idx) && tasks[idx]) {
        tasks[idx].done = input.checked;
        item.todo = serializeTodo(tasks);

        const txt = li.querySelector('.todo-text');
        if (txt) txt.classList.toggle('todo-done', input.checked);

        // Sauvegarde TODO
        fetch('/update_timetable_event', {
            method: 'POST',
            headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': document.querySelector('meta[name="csrf-token"]').getAttribute('content')
            },
            body: JSON.stringify({
            event: window.selectedEvent,
            year: window.selectedYear,
            date: eventItem.dataset.date,
            _id: item._id,
            todo: item.todo
            })
        })
        .then(r => r.json())
        .then(res => {
            if (!res.success && typeof showDynamicFlashMessage === 'function') {
            showDynamicFlashMessage("Échec de la sauvegarde TODO", "error");
            }

            // 🔵 ICI : on vérifie si tout est coché puis on marque prêt
            const allDone = tasks.length > 0 && tasks.every(t => t.done);

            // OPTION A : si tu as une route /set_preparation_ready
            if (allDone && (item.preparation_checked || "").toLowerCase() !== "true") {
            fetch('/set_preparation_ready', {
                method: 'POST',
                headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': document.querySelector('meta[name="csrf-token"]').getAttribute('content')
                },
                body: JSON.stringify({
                id: item._id,
                event: window.selectedEvent,
                year: window.selectedYear,
                date: eventItem.dataset.date
                })
            }).then(()=> {
                item.preparation_checked = "true";
                applyPreparationStatus(eventItem, "true");
            });
            }
        })
        .catch(err => console.error("Save TODO failed:", err));
        }
    });
    }

    // Attacher l'écouteur sur le bouton d'extension
    const expandBtn = eventItem.querySelector(".expand-btn");
    if (expandBtn) {
        expandBtn.addEventListener("click", function(e) {
            e.stopPropagation(); // Empêche l'ouverture de la modale lors du clic sur ce bouton
            toggleDetails(e, this);
        });
    }

    // Rendre la vignette cliquable pour ouvrir la modale détaillée (en dehors du bouton d'extension)
    eventItem.addEventListener('click', function(e) {
    if (!e.target.closest(".expand-btn")) {
        openEventDrawer(date, item);
    }
    });
    
    return eventItem;
}

function createClusterItem(date, cluster) {
  const cfg = CLUSTER_CONFIG[cluster.type];
  const count = cluster.items.length;
  const kindLabel = cluster.kind === 'close' ? 'Fermeture' : 'Ouverture';
  const timeInfo = cluster.time || 'TBC';

  const clusterPrep = getClusterPrepStatus(cluster);
    const clusterPrepHtml = clusterPrep
    ? `<span class="prep-chip prep-${clusterPrep}" title="Préparation : ${getPrepLabel(clusterPrep)}">${getPrepLabel(clusterPrep)}</span>`
    : "";

  const el = document.createElement('div');
  el.classList.add('event-item');
  el.innerHTML = `
    <div class="event-summary">
      <div class="event-title">
        <span class="material-icons">${cfg.icon}</span>
        <h5>${cfg.label} — ${kindLabel} ${timeInfo} (${count})</h5>
      </div>
      <div class="event-time">
        <p class="time-info">${timeInfo}</p>
        <p class="event-location">Regroupement</p>
        ${clusterPrepHtml}
      </div>
      <div class="buttons-container">
        <button class="expand-btn"><span class="material-icons">expand_more</span></button>
      </div>
    </div>
    <div class="toggle-content">
      <ul class="cluster-list" style="list-style:none; padding-left:0; margin:0;">
        ${cluster.items.map(ch => {
          const title = (ch.activity||'Sans titre').split('/')[0].trim();
          const place = (ch.place||'—').split('/')[0].trim();
          const hours = (ch.start && ch.start!=='TBC' ? ch.start : '') +
                        (ch.end   && ch.end  !=='TBC' ? (' - '+ch.end) : '');
          return `
            <li class="cluster-line" data-child-id="${ch._id}" style="display:flex; gap:8px; align-items:center; padding:6px 0; border-bottom:1px solid rgba(255,255,255,0.08); cursor:pointer;">
              <span class="material-icons" style="font-size:18px; opacity:.8;">chevron_right</span>
              <div style="flex:1;">
                <div style="font-weight:700;">${title}</div>
                <div style="opacity:.8; font-size:12px;">${place} ${hours?('• '+hours):''}</div>
              </div>
            </li>`;
        }).join('')}
      </ul>
    </div>
  `;

  // expand/collapse
  el.querySelector('.expand-btn')?.addEventListener('click', (e)=>{
    e.stopPropagation();
    toggleDetails(e, e.currentTarget);
  });

  // clic sur une sous-ligne -> ouvrir le drawer de l’item
  el.querySelectorAll('.cluster-line').forEach(li=>{
    li.addEventListener('click', ()=>{
      const id = li.getAttribute('data-child-id');
      const it = cluster.items.find(x => String(x._id) === String(id));
      if (it) openEventDrawer(date, it);
    });
  });

  // clic sur la carte -> expand
  el.addEventListener('click', (e)=>{
    if (!e.target.closest('.expand-btn')) {
      el.classList.toggle('expanded');
      const icon = el.querySelector('.expand-btn .material-icons');
      icon.textContent = el.classList.contains('expanded') ? 'expand_less' : 'expand_more';
    }
  });

  return el;
}

// Fonction pour basculer l'affichage des détails (expand/collapse)
function toggleDetails(e, button) {
    const eventItem = button.closest('.event-item');
    if (!eventItem) return;
    eventItem.classList.toggle('expanded');
    const icon = button.querySelector('.material-icons');
    if (eventItem.classList.contains('expanded')) {
        icon.textContent = 'expand_less';
    } else {
        icon.textContent = 'expand_more';
    }
}

// Fonction pour récupérer et afficher le timetable dans la timeline
function fetchTimetable() {
    if (!window.selectedEvent || !window.selectedYear) {
        console.error("Les variables globales 'selectedEvent' et 'selectedYear' doivent être définies.");
        return;
    }
    const url = '/timetable?event=' + encodeURIComponent(window.selectedEvent) + '&year=' + encodeURIComponent(window.selectedYear);
    
    fetch(url)
        .then(response => response.json())
        .then(data => {
            const eventList = document.getElementById("event-list");
            if (eventList) eventList.innerHTML = "";  // <-- reset pour éviter les accumulations

            const sectionsByDate = {}; // Pour stocker les sections par date

            if (data.data) {
                // 👇 nettoie les paires open/close à 00:00 (même jour + minuit croisé)
               removeRedundantOpenClosePairs(data.data, { onlyMidnight: false });

                Object.keys(data.data).sort().forEach(date => {
                    const items = data.data[date];

                    const dateSection = document.createElement("div");
                    dateSection.classList.add("timetable-date-section");
                    sectionsByDate[date] = dateSection;

                    // Créer un container pour le header de la date
                    const dateHeaderContainer = document.createElement("div");
                    dateHeaderContainer.classList.add("date-header-container");

                    const d = new Date(date);
                    const dateHeader = document.createElement("h5");
                    dateHeader.textContent = d.toLocaleDateString("fr-FR");
                    dateHeaderContainer.appendChild(dateHeader);

                    // Vérifier si la journée a des dates ouvertes au public
                    const bannerInfo = getPublicBannerForDateStr(date); // <- date au format YYYY-MM-DD
                    const banner = document.createElement("p");
                    banner.textContent = bannerInfo.text;
                    banner.classList.add(bannerInfo.className);
                    dateHeaderContainer.appendChild(banner);

                    dateSection.appendChild(dateHeaderContainer);

                    // 1) Regrouper
                    const { clusters, rest } = groupByClusters(items);

                    // 2) Construire une liste combinée avec minute de tri
                    const combined = [
                        ...clusters.map(c => ({ kind:'cluster', minute: getClusterSortMinute(c), data: c })),
                        ...rest.map(it => ({ kind:'item',    minute: getItemSortMinute(it),    data: it })),
                    ];

                    // 3) Trier: minute croissante, puis clusters avant items, puis label alpha
                    combined.sort((a,b)=>{
                        if (a.minute !== b.minute) return a.minute - b.minute;
                        if (a.kind !== b.kind) return a.kind === 'cluster' ? -1 : 1; // option: cluster d'abord
                        const la = a.kind==='cluster'
                        ? `${a.data.type}|${a.data.kind}|${a.data.time||'zzz'}`.toLowerCase()
                        : labelForItem(a.data);
                        const lb = b.kind==='cluster'
                        ? `${b.data.type}|${b.data.kind}|${b.data.time||'zzz'}`.toLowerCase()
                        : labelForItem(b.data);
                        return la.localeCompare(lb);
                    });

                    // 4) Rendu chronologique
                    combined.forEach(node=>{
                        if (node.kind === 'cluster') {
                        const card = createClusterItem(date, node.data);
                        dateSection.appendChild(card);
                        } else {
                        // on garde le filtre "General / Ouverture au public" si nécessaire
                        const it = node.data;
                        if (it.category === "General" && it.activity?.trim()?.toLowerCase() === "ouverture au public") return;
                        const card = createEventItem(date, it);
                        dateSection.appendChild(card);
                        }
                    });

                    eventList.appendChild(dateSection);
                });
            } else {
                eventList.innerHTML += "<p>Aucune donnée de timetable disponible.</p>";
            }
        })
        .catch(error => console.error("Erreur lors de la récupération du timetable :", error));
}

/**
function getTimeForSort(item) {
    // Si start est défini, non vide et différent de "TBC", on l'utilise
    if (item.start && item.start.trim() !== "" && item.start.toUpperCase() !== "TBC") {
        return timeToMinutes(item.start);
    }
    // Sinon, si end est défini et valide, on l'utilise
    if (item.end && item.end.trim() !== "" && item.end.toUpperCase() !== "TBC") {
        return timeToMinutes(item.end);
    }
    // Sinon, on retourne Infinity pour le classer en fin
    return Infinity;
} */

// Nouvelle fonction pour récupérer les paramètres (paramétrage) via POST
function fetchParametrage() {
    if (!window.selectedEvent || !window.selectedYear) {
    console.error("Les variables globales 'selectedEvent' et 'selectedYear' doivent être définies.");
    return Promise.resolve(null);
    }
    return fetch('/get_parametrage?event=' + encodeURIComponent(window.selectedEvent) + '&year=' + encodeURIComponent(window.selectedYear))
    .then(response => response.json())
    .then(data => {
        window.parametrage = data;                // l'API te renvoie déjà le champ "data"
        window.publicDatesMap = buildPublicDatesMap(data); // 👈 IMPORTANT
        console.log('publicDatesMap:', window.publicDatesMap);
        return data;
    })
    .catch(error => {
        console.error("Erreur lors de la récupération des paramètres :", error);
        window.publicDatesMap = {};               // évite les undefined
        return Promise.resolve(null);
    });
}


// Exemple de fonction pour ouvrir la modale détaillée pour un item
function openTimetableItemModal(date, item) {
    console.log("Ouverture de la modale pour la date", date, "et l'item :", item);
    // Redirige vers le tiroir latéral droit si disponible
    if (window.openEventDrawer) {
        // On passe la date aussi (pratique pour l'affichage)
        window.openEventDrawer({ ...item, date });
        return;
    }
    // Fallback simple si le drawer n'est pas chargé
    alert("Détails événement indisponibles (drawer non chargé).");
}

// Écouteur sur le bouton HUD pour lancer fetchTimetable()
document.addEventListener('DOMContentLoaded', function() {
  const hudButton = document.getElementById("hud-button");
  if (hudButton) {
    hudButton.addEventListener("click", function() {
      // ⚠️ d’abord le paramétrage, ensuite la timeline (pour avoir publicDatesMap prêt)
      fetchParametrage().then(() => {
        fetchTimetable();
        updateGlobalCounter();
      });
    });
  } else {
    console.error("Le bouton avec l'ID 'hud-button' n'a pas été trouvé.");
  }
});

/////////////////////////////////////////////////////////////////////////////////////////////////////
// FONCTION AJOUT
/////////////////////////////////////////////////////////////////////////////////////////////////////

document.addEventListener('DOMContentLoaded', function(){
    // Références aux éléments
    const addEventButton = document.getElementById('add-event-button');
    const addEventModal = document.getElementById('addEventModal');
    const closeAddEvent = document.getElementById('closeAddEvent');
    const cancelAddEvent = document.getElementById('cancelAddEvent');
    const addEventForm = document.getElementById('addEventForm');
    const categorySelect = document.getElementById('category');

    // Fonction pour ouvrir la modale en ajoutant la classe "show"
    function openModal(modal) {
        modal.style.display = 'block';
        // Permettre la transition définie dans le CSS (opacity et scale)
        setTimeout(() => {
            modal.classList.add('show');
        }, 10);
    }
    
    // Fonction pour fermer la modale
    function closeModal(modal) {
        modal.classList.remove('show');
        // Après la transition, masquer la modale
        setTimeout(() => {
            modal.style.display = 'none';
        }, 300);
    }
    
    // Ouvrir la modale lors du clic sur le bouton "Ajouter un événement"
    addEventButton.addEventListener('click', function(){
        // Charger les catégories depuis le serveur
        fetch('/get_timetable_categories?event=' + encodeURIComponent(window.selectedEvent) + '&year=' + encodeURIComponent(window.selectedYear))
        .then(response => response.json())
        .then(data => {
            categorySelect.innerHTML = '';
            data.categories.forEach(cat => {
                const option = document.createElement('option');
                option.value = cat;
                option.textContent = cat;
                categorySelect.appendChild(option);
            });
        })
        .catch(err => console.error("Erreur chargement catégories:", err));    
        
        openModal(addEventModal);
    });
    
    // Fermer la modale au clic sur la croix ou le bouton Annuler
    closeAddEvent.addEventListener('click', () => {
        window.formMode = 'add';
        window.editingItemId = null;
        const title = addEventModal.querySelector('h3');
        if (title) title.textContent = "Ajouter un événement à la Timetable";
        closeModal(addEventModal);
    });
    cancelAddEvent.addEventListener('click', () => {
        window.formMode = 'add';
        window.editingItemId = null;
        const title = addEventModal.querySelector('h3');
        if (title) title.textContent = "Ajouter un événement à la Timetable";
        closeModal(addEventModal);
    });
    
    // Soumission du formulaire (ADD ou EDIT)
    addEventForm.addEventListener('submit', function(e){
        e.preventDefault();

        const isEdit = (window.formMode === 'edit');
        const endpoint = isEdit ? '/update_timetable_event' : '/add_timetable_event';

        // Récupérer les valeurs du formulaire
        const payload = {
            event: window.selectedEvent || '24H MOTOS',
            year: window.selectedYear || '2025',
            date: document.getElementById('event-date').value,
            start: document.getElementById('start-time').value,
            end: document.getElementById('end-time').value,
            duration: document.getElementById('duration').value,
            category: document.getElementById('category').value,
            activity: document.getElementById('activity').value,
            place: document.getElementById('place').value,
            department: document.getElementById('department').value,
            remark: document.getElementById('remark').value,
            type: "Timetable",
            origin: isEdit ? "manual-edit" : "manual"
        };

        // Inclure l'ID en édition
        if (isEdit) {
            const hiddenId = addEventForm.querySelector('input[name=\"_id\"]');
            const idVal = hiddenId ? hiddenId.value : (window.editingItemId || null);
            if (idVal) payload._id = idVal;
        }

        fetch(endpoint, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': document.querySelector('meta[name=\"csrf-token\"]').getAttribute('content')
            },
            body: JSON.stringify(payload)
        })
        .then(r => r.json())
        .then(data => {
            if (data.success) {
                showDynamicFlashMessage(isEdit ? "Événement modifié avec succès" : "Événement ajouté avec succès", "success");
                closeModal(addEventModal);

                // Reset mode vers ADD et texte titre
                window.formMode = 'add';
                window.editingItemId = null;
                const title = addEventModal.querySelector('h3');
                if (title) title.textContent = "Ajouter un événement à la Timetable";

                // Rafraîchir la timeline
                const eventList = document.getElementById("event-list");
                if (eventList) eventList.innerHTML = "";
                fetchTimetable();
            } else {
                showDynamicFlashMessage(data.message || "Erreur lors de l'enregistrement", "error");
            }
        })
        .catch(err => {
            console.error("Erreur lors de l'enregistrement:", err);
            showDynamicFlashMessage("Erreur lors de l'enregistrement", "error");
        });
    });
    
    // Fermer la modale si l'utilisateur clique en dehors du contenu de la modale
    window.addEventListener('click', function(e){
        if(e.target == addEventModal){
            closeModal(addEventModal);
        }
    });
});

/**************************************************************
 * DRAWER ÉVÉNEMENT (TODO + Préparation + Édition inline)
 **************************************************************/
let _drawerCurrent = { date: null, item: null };   // contexte courant
const drawerEl      = document.getElementById('event-drawer');
const drawerBodyEl  = document.getElementById('event-drawer-body');
const drawerTitleEl = document.getElementById('drawer-title');
const drawerOverlay = document.getElementById('event-drawer-overlay');
const drawerClose   = document.getElementById('drawer-close');
const btnEdit       = document.getElementById('drawer-edit');
const btnDup        = document.getElementById('drawer-duplicate');
const btnDel        = document.getElementById('drawer-delete');

function openEventDrawer(date, item) {
  _drawerCurrent = { date, item: structuredClone(item) }; // copie pour édition locale
  renderDrawerView();
  drawerEl.classList.add('open');
  drawerOverlay.classList.add('show');
  drawerEl.setAttribute('aria-hidden', 'false');
}
function closeEventDrawer() {
  drawerEl.classList.remove('open');
  drawerOverlay.classList.remove('show');
  drawerEl.setAttribute('aria-hidden', 'true');
}
drawerOverlay?.addEventListener('click', closeEventDrawer);
drawerClose?.addEventListener('click', closeEventDrawer);

/* --- Helpers TODO --- */
function parseTodos(todoField) {
  if (!todoField) return [];
  if (Array.isArray(todoField)) return todoField.map(s => s.toString());
  return todoField
    .toString()
    .split(/\r?\n/)              // lignes
    .map(s => s.trim())
    .filter(Boolean);
}
function stringifyTodos(list) {
  return list.join('\n');
}

/* --- Vue en lecture --- */
function renderDrawerView() {
  const it = _drawerCurrent.item;
  drawerTitleEl.textContent = it.activity || 'Événement';
  const todoArray = parseTodos(it.todo);

  const prep = (it.preparation_checked ?? "").toString().toLowerCase(); // "", "progress", "true"
  const prepLabel =
    prep === 'true' ? 'Prête'
  : prep === 'progress' ? 'En cours'
  : 'Non';

  drawerBodyEl.innerHTML = `
    <div class="field">
      <div class="label">Date</div>
      <div class="value">${_drawerCurrent.date}</div>
    </div>
    <div class="field">
      <div class="label">Heures</div>
      <div class="value">${(it.start && it.start!=='TBC')?it.start:'—'} ${(it.end && it.end!=='TBC')?(' - '+it.end):''}</div>
    </div>
    <div class="field">
      <div class="label">Catégorie</div>
      <div class="value">${it.category || '—'}</div>
    </div>
    <div class="field">
      <div class="label">Lieu</div>
      <div class="value">${it.place || '—'}</div>
    </div>
    <div class="field">
      <div class="label">Département</div>
      <div class="value">${it.department || '—'}</div>
    </div>
    <div class="field">
      <div class="label">Remarques</div>
      <div class="value">${it.remark || '—'}</div>
    </div>

    <div class="field">
      <div class="label">Préparation</div>
      <div class="value"><span class="prep-pill prep-${prep || 'none'}">${prepLabel}</span></div>
    </div>

    <div class="field">
      <div class="label">TODO</div>
      ${todoArray.length ? `
        <ul class="todo-list">
          ${todoArray.map((line, idx) => {
            const done = /^\s*(\[x\]|x\s)/i.test(line);
            const clean = line.replace(/^\s*(\[x\]|x\s)\s*/i, '');
            return `
              <li>
                <label class="todo-item">
                  <input type="checkbox" data-idx="${idx}" ${done ? 'checked':''}/>
                  <span>${clean}</span>
                </label>
              </li>`;
          }).join('')}
        </ul>
        <div class="todo-actions">
          <button class="btn btn-secondary" id="todo-add-line">Ajouter une tâche</button>
          <button class="btn btn-secondary" id="todo-clear-done">Supprimer tâches faites</button>
          <button class="btn btn-primary"   id="todo-save">Enregistrer TODO</button>
        </div>
      ` : `
        <div class="empty-todo">
          Aucune tâche. <button class="btn btn-secondary" id="todo-add-first">Ajouter</button>
        </div>
      `}
    </div>
  `;

  // Wiring TODO interactions
  drawerBodyEl.querySelectorAll('input[type="checkbox"][data-idx]').forEach(cb => {
    cb.addEventListener('change', () => {
      const idx  = Number(cb.dataset.idx);
      const list = parseTodos(_drawerCurrent.item.todo);
      const line = list[idx] || '';
      const clean = line.replace(/^\s*(\[x\]|x\s)\s*/i, '');
      list[idx] = cb.checked ? `[x] ${clean}` : clean;
      _drawerCurrent.item.todo = stringifyTodos(list);
    });
  });

  const addLine = drawerBodyEl.querySelector('#todo-add-line');
  const addFirst = drawerBodyEl.querySelector('#todo-add-first');
  const clearDone = drawerBodyEl.querySelector('#todo-clear-done');
  const saveTodo = drawerBodyEl.querySelector('#todo-save');

  addLine?.addEventListener('click', () => {
    const list = parseTodos(_drawerCurrent.item.todo);
    const txt = prompt('Nouvelle tâche :');
    if (txt && txt.trim()) {
      list.push(txt.trim());
      _drawerCurrent.item.todo = stringifyTodos(list);
      renderDrawerView(); // re-render
    }
  });
  addFirst?.addEventListener('click', () => {
    const list = parseTodos(_drawerCurrent.item.todo);
    const txt = prompt('Nouvelle tâche :');
    if (txt && txt.trim()) {
      list.push(txt.trim());
      _drawerCurrent.item.todo = stringifyTodos(list);
      renderDrawerView();
    }
  });
  clearDone?.addEventListener('click', () => {
    const list = parseTodos(_drawerCurrent.item.todo).filter(l => !/^\s*(\[x\]|x\s)/i.test(l));
    _drawerCurrent.item.todo = stringifyTodos(list);
    renderDrawerView();
  });
  saveTodo?.addEventListener('click', () => {
    saveUpdate(_drawerCurrent.date, _drawerCurrent.item);
  });
}

/* --- Vue édition inline --- */
function renderDrawerEdit() {
  const it = _drawerCurrent.item;
  const todoText = stringifyTodos(parseTodos(it.todo));
  drawerBodyEl.innerHTML = `
    <div class="field">
      <div class="label">Date</div>
      <input class="form-input" type="date" id="edit-date" value="${_drawerCurrent.date}">
    </div>
    <div class="field">
      <div class="label">Début</div>
      <input class="form-input" type="text" id="edit-start" value="${(it.start && it.start!=='TBC')?it.start:''}" placeholder="HH:MM ou TBC">
    </div>
    <div class="field">
      <div class="label">Fin</div>
      <input class="form-input" type="text" id="edit-end" value="${(it.end && it.end!=='TBC')?it.end:''}" placeholder="HH:MM ou TBC">
    </div>
    <div class="field">
      <div class="label">Durée</div>
      <input class="form-input" type="text" id="edit-duration" value="${it.duration||''}">
    </div>
    <div class="field">
      <div class="label">Catégorie</div>
      <select class="form-input" id="edit-category"></select>
    </div>
    <div class="field">
      <div class="label">Activité</div>
      <input class="form-input" type="text" id="edit-activity" value="${it.activity||''}">
    </div>
    <div class="field">
      <div class="label">Lieu</div>
      <input class="form-input" type="text" id="edit-place" value="${it.place||''}">
    </div>
    <div class="field">
      <div class="label">Département</div>
      <input class="form-input" type="text" id="edit-dept" value="${it.department||''}">
    </div>
    <div class="field">
      <div class="label">Remarques</div>
      <textarea class="form-input" id="edit-remark">${it.remark||''}</textarea>
    </div>
    <div class="field">
      <div class="label">TODO (une tâche par ligne)</div>
      <textarea class="form-input" id="edit-todo" rows="6">${todoText}</textarea>
    </div>
    <div class="field">
      <div class="label">État de préparation</div>
      <select class="form-input" id="edit-prep">
        <option value="">Non</option>
        <option value="progress">En cours</option>
        <option value="true">Prête</option>
      </select>
    </div>
    <div style="display:flex; gap:8px;">
      <button id="edit-cancel" class="btn btn-secondary" style="flex:1;">Annuler</button>
      <button id="edit-save"   class="btn btn-primary"   style="flex:1;">Enregistrer</button>
    </div>
  `;

  // Remplir catégories
  const catSelect = drawerBodyEl.querySelector('#edit-category');
  catSelect.innerHTML = '';
  (window.categories || []).forEach(c => {
    const opt = document.createElement('option');
    opt.value = c; opt.textContent = c;
    if (c === _drawerCurrent.item.category) opt.selected = true;
    catSelect.appendChild(opt);
  });

  // Prépa
  const prepSel = drawerBodyEl.querySelector('#edit-prep');
  prepSel.value = (it.preparation_checked ?? '').toString().toLowerCase();

  drawerBodyEl.querySelector('#edit-cancel').addEventListener('click', renderDrawerView);
  drawerBodyEl.querySelector('#edit-save').addEventListener('click', () => {
    const newItem = {
      ..._drawerCurrent.item,
      start:   (drawerBodyEl.querySelector('#edit-start').value || 'TBC').trim(),
      end:     (drawerBodyEl.querySelector('#edit-end').value || 'TBC').trim(),
      duration:drawerBodyEl.querySelector('#edit-duration').value.trim(),
      category:drawerBodyEl.querySelector('#edit-category').value,
      activity:drawerBodyEl.querySelector('#edit-activity').value.trim(),
      place:   drawerBodyEl.querySelector('#edit-place').value.trim(),
      department: drawerBodyEl.querySelector('#edit-dept').value.trim(),
      remark:  drawerBodyEl.querySelector('#edit-remark').value.trim(),
      todo:    drawerBodyEl.querySelector('#edit-todo').value, // texte multi-lignes
      preparation_checked: drawerBodyEl.querySelector('#edit-prep').value
    };
    const newDate = drawerBodyEl.querySelector('#edit-date').value || _drawerCurrent.date;
    _drawerCurrent = { date: newDate, item: newItem };
    saveUpdate(newDate, newItem, true);
  });
}

/* --- Boutons pied de drawer --- */
btnEdit?.addEventListener('click', async () => {
  // s’assurer qu’on a les catégories
  if (!Array.isArray(window.categories) || window.categories.length === 0) {
    await loadCategories?.().catch(()=>{});
  }
  renderDrawerEdit();
});
btnDup?.addEventListener('click', () => {
  duplicateCurrent();
});
btnDel?.addEventListener('click', () => {
  deleteCurrent();
});

/* --- Persistances serveur --- */
// NB: ces routes doivent exister côté Flask :
//  - POST /update_timetable_event
//  - POST /delete_timetable_event
//  - POST /add_timetable_event
//  - POST /set_preparation_progress (optionnel utilisé ailleurs)
//  - POST /set_preparation_ready   (optionnel)
function saveUpdate(dateStr, item, closeAfter = false) {
  const payload = {
    event:      window.selectedEvent,
    year:       window.selectedYear,
    date:       dateStr,
    _id:        item._id,
    param_id:   item.param_id || null,
    start:      item.start || 'TBC',
    end:        item.end || 'TBC',
    duration:   item.duration || '',
    category:   item.category || '',
    activity:   item.activity || '',
    place:      item.place || '',
    department: item.department || '',
    remark:     item.remark || '',
    todo:       item.todo || '',
    preparation_checked: (item.preparation_checked ?? '').toString().toLowerCase()
  };
  fetch('/update_timetable_event', {
    method: 'POST',
    headers: {
      'Content-Type':'application/json',
      'X-CSRFToken': document.querySelector('meta[name="csrf-token"]').getAttribute('content')
    },
    body: JSON.stringify(payload)
  })
  .then(r=>r.json())
  .then(res=>{
    if (res.success) {
      showDynamicFlashMessage("Mise à jour réussie", "success");
      fetchTimetable(); // rafraîchir la liste
      closeAfter && closeEventDrawer();
      // recharger la vue lecture avec l’objet mis à jour
      !_drawerCurrent || renderDrawerView();
    } else {
      showDynamicFlashMessage("Erreur lors de l'enregistrement", "error");
    }
  })
  .catch(()=> showDynamicFlashMessage("Erreur réseau", "error"));
}

function deleteCurrent() {
  const it = _drawerCurrent.item;
  if (!it || !it._id) return;
  if (!confirm("Supprimer définitivement cet événement ?")) return;

  const payload = {
    event: window.selectedEvent,
    year:  window.selectedYear,
    date:  _drawerCurrent.date,
    _id:   it._id
  };
  fetch('/delete_timetable_event', {
    method: 'POST',
    headers: {
      'Content-Type':'application/json',
      'X-CSRFToken': document.querySelector('meta[name="csrf-token"]').getAttribute('content')
    },
    body: JSON.stringify(payload)
  })
  .then(r=>r.json())
  .then(res=>{
    if (res.success) {
      showDynamicFlashMessage("Événement supprimé", "success");
      fetchTimetable();
      closeEventDrawer();
    } else {
      showDynamicFlashMessage("Suppression impossible", "error");
    }
  })
  .catch(()=> showDynamicFlashMessage("Erreur réseau", "error"));
}

function duplicateCurrent() {
  const it = _drawerCurrent.item;
  if (!it) return;

  const payload = {
    event: window.selectedEvent,
    year:  window.selectedYear,
    date:  _drawerCurrent.date, // tu peux proposer une autre date via prompt si besoin
    start: it.start || 'TBC',
    end:   it.end   || 'TBC',
    duration: it.duration || '',
    category: it.category || '',
    activity: (it.activity || '') + ' (copie)',
    place: it.place || '',
    department: it.department || '',
    remark: it.remark || '',
    type: "Timetable",
    origin: "manual",
    todo: it.todo || '',
    preparation_checked: "" // on remet à blanc pour la copie
  };

  fetch('/add_timetable_event', {
    method: 'POST',
    headers: {
      'Content-Type':'application/json',
      'X-CSRFToken': document.querySelector('meta[name="csrf-token"]').getAttribute('content')
    },
    body: JSON.stringify(payload)
  })
  .then(r=>r.json())
  .then(res=>{
    if (res.success) {
      showDynamicFlashMessage("Événement dupliqué", "success");
      fetchTimetable();
    } else {
      showDynamicFlashMessage("Erreur lors de la duplication", "error");
    }
  })
  .catch(()=> showDynamicFlashMessage("Erreur réseau", "error"));
}