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
  const forceReady = (item.preparation_checked || "").toString().toLowerCase() === "true";
  const tasks = splitTodo(item.todo || "");
  if (tasks.length === 0) return "";

  // ⬇️ si prêt -> on force l’affichage comme coché
  const finalTasks = forceReady ? tasks.map(t => ({...t, done:true})) : tasks;

  const lis = finalTasks.map((t, idx) => `
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
function removeRedundantOpenClosePairs(byDate, { mode = 'midnight' } = {}) {
  if (!byDate || typeof byDate !== 'object') return;

  const dates = Object.keys(byDate).sort();
  const normPlace = s => norm(s || '').replace(/\s+/g, ' ').trim();
  const detectType = it => {
    if (CLUSTER_CONFIG.parking.match(it)) return 'parking';
    if (CLUSTER_CONFIG.accueil.match(it)) return 'accueil';
    if (CLUSTER_CONFIG.portes.match(it))  return 'portes';
    return null;
  };

  if (mode === 'off') return;

  const toDelete = {};
  const wantThisTime = (t) => (
    mode === 'midnight' ? t === '00:00'
  : mode === 'all'      ? (t && t !== 'TBC')
  : false);

  // 1) Same-day: supprime open & close à la même heure pour même type+lieu
  dates.forEach(date => {
    const arr = byDate[date] || [];
    const bucket = {};
    arr.forEach(it => {
      const type = detectType(it);
      const kind = getOpenCloseKind(it);
      if (!type || !kind) return;
      const timeKey = getClusterTimeKey(it, kind);
      if (!wantThisTime(timeKey)) return;
      const key = `${type}|${normPlace(it.place||'')}|${timeKey}`;
      (bucket[key] ||= { open:[], close:[] })[kind].push(it);
    });
    Object.values(bucket).forEach(group => {
      if (group.open.length && group.close.length) {
        group.open.concat(group.close).forEach(it => {
          if (it?._id) (toDelete[date] ||= new Set()).add(String(it._id));
        });
      }
    });
  });

  // 2) Cross-day: ça n’a de sens qu’à minuit
  if (mode === 'midnight') {
    for (let i = 0; i < dates.length - 1; i++) {
      const d0 = dates[i], d1 = dates[i+1];
      const a0 = (byDate[d0] || []).filter(it => getOpenCloseKind(it) === 'close' && getClusterTimeKey(it, 'close') === '00:00');
      const a1 = (byDate[d1] || []).filter(it => getOpenCloseKind(it) === 'open'  && getClusterTimeKey(it, 'open')  === '00:00');

      if (!a0.length || !a1.length) continue;

      const mapOpen = new Map();
      a1.forEach(it => {
        const type = detectType(it); if (!type) return;
        const key = `${type}|${normPlace(it.place||'')}`;
        (mapOpen.get(key) || mapOpen.set(key, [])).push(it);
      });

      a0.forEach(itClose => {
        const type = detectType(itClose); if (!type) return;
        const key = `${type}|${normPlace(itClose.place||'')}`;
        const matches = mapOpen.get(key);
        if (matches?.length) {
          if (itClose._id) (toDelete[d0] ||= new Set()).add(String(itClose._id));
          matches.forEach(itOpen => itOpen?._id && (toDelete[d1] ||= new Set()).add(String(itOpen._id)));
        }
      });
    }
  }

  // 3) Apply
  dates.forEach(date => {
    const del = toDelete[date];
    if (del?.size) byDate[date] = (byDate[date] || []).filter(it => !del.has(String(it._id)));
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
       : status === "late"     ? "En retard"
       : status === "done"     ? "Terminé"
       : "";
}

// --- statut "runtime" (en fonction de l'heure courante) ---
function getRuntimeDisplayStatus(baseStatus, item, cardDateStr, nowYMD, nowMin){
  // si date passée → forcément “dépassé”
  if (cardDateStr < nowYMD) {
    if (baseStatus === 'ready') return 'done';
    if (baseStatus === 'progress' || baseStatus === 'none' || !baseStatus) return 'late';
    return baseStatus || 'none';
  }
  // si date future → pas d'effet
  if (cardDateStr > nowYMD) return baseStatus || 'none';

  // même jour
  const startOk = item.start && item.start.toUpperCase() !== 'TBC';
  const endOk   = item.end   && item.end.toUpperCase()   !== 'TBC';
  let refMinute = Infinity;
  if (startOk) refMinute = timeToMinutes(item.start);
  else if (endOk) refMinute = timeToMinutes(item.end);

  if (Number.isFinite(refMinute) && nowMin >= refMinute) {
    if (baseStatus === 'ready') return 'done';
    if (baseStatus === 'progress' || baseStatus === 'none' || !baseStatus) return 'late';
  }
  return baseStatus || 'none';
}

// Statut agrégé *runtime* d’un cluster (pire des statuts de ses enfants)
function getClusterDisplayStatus(cluster, dateStr, nowYMD, nowMin) {
  if (!cluster?.items?.length) return null;
  let worst = null, worstScore = -1;
  for (const ch of cluster.items) {
    const s = getItemDisplayStatus(ch, dateStr, nowYMD, nowMin); // late/none/progress/ready/done
    const score = statusPriorityValue(s);
    if (score > worstScore) { worstScore = score; worst = s; }
    if (worst === 'late') break; // on ne peut pas faire "pire"
  }
  return worst || null;
}

// ordre de sévérité (plus grand = pire)
function statusPriorityValue(s) {
  switch ((s || '').toLowerCase()) {
    case 'late':     return 5; // pire
    case 'none':     return 4;
    case 'progress': return 3;
    case 'ready':    return 2;
    case 'done':     return 1; // meilleur
    default:         return 0; // inconnu
  }
}

// statut "base" (métier) -> déjà getPrepStatus(item)
// statut "live" (intégrant l'heure) pour un *item*
function getItemDisplayStatus(item, dateStr, nowYMD, nowMin) {
  const base = getPrepStatus(item) || 'none';
  return getRuntimeDisplayStatus(base, item, dateStr, nowYMD, nowMin);
}

function ymdLocal(d){
  const Y = d.getFullYear();
  const M = String(d.getMonth()+1).padStart(2,'0');
  const D = String(d.getDate()).padStart(2,'0');
  return `${Y}-${M}-${D}`;
}

function requireIdOrWarn(item) {
  if (!item || !item._id) {
    typeof showDynamicFlashMessage === 'function' &&
      showDynamicFlashMessage("Événement incomplet (id manquant)", "error");
    return false;
  }
  return true;
}

function logDupesOnce(list, date) {
  const counts = {};
  for (const it of (list || [])) {
    const id = String(it?._id ?? '');
    if (!id) continue;
    counts[id] = (counts[id] || 0) + 1;
  }
  const dupIds = Object.keys(counts).filter(id => counts[id] > 1);
  if (dupIds.length) {
    console.warn(`[TT DUP PAYLOAD] ${date} → ${dupIds.length} doublon(s)`, { date, dupIds, counts });
  } else {
    console.debug(`[TT OK PAYLOAD] ${date} (aucun doublon détecté)`);
  }
}

/////////////////////////////////////////////////////////////////////////////////////////////////////
// AFFICHAGE
/////////////////////////////////////////////////////////////////////////////////////////////////////

function applyPreparationStatus(cardEl, statusStr) {
  const status = (statusStr || '').toString().toLowerCase();
  const label = status === 'true' || status === 'ready' ? 'Prête'
              : status === 'progress' ? 'En cours'
              : 'Non';

  // pastille dans le résumé
  let chip = cardEl.querySelector('.prep-chip');
  if (!chip) {
    const timeCol = cardEl.querySelector('.event-time');
    if (timeCol) {
      chip = document.createElement('span');
      chip.className = 'prep-chip';
      timeCol.appendChild(chip);
    }
  }
  if (chip) {
    // applique le statut de base, puis le runtime par dessus
    const base = (status === 'true' ? 'ready' : status || 'none');
    chip.className = `prep-chip prep-${base}`;
    chip.textContent = getPrepLabel(base);
    const cardDate = cardEl.closest('.timetable-date-section')?.dataset?.date || null;
    const now = TimelineClock.get();
    const disp = (cardDate && cardEl.__itemData)
      ? getRuntimeDisplayStatus(base, cardEl.__itemData, cardDate, ymdLocal(now), now.getHours()*60+now.getMinutes())
      : base;
    chip.className = `prep-chip prep-${disp}`;
    chip.textContent = getPrepLabel(disp);
  }

  // cocher visuellement toutes les cases du sticky si présent
  const sticky = cardEl.querySelector('.todo-sticky');
  if (sticky) {
    sticky.querySelectorAll('input.todo-checkbox').forEach(cb => {
      cb.checked = (status === 'true');
      const li = cb.closest('li');
      li?.querySelector('.todo-text')?.classList.toggle('todo-done', cb.checked);
    });
  }
}

// Fonction pour créer une vignette d'événement dans la timeline avec affichage en deux colonnes
function createEventItem(date, item) {
    const eventItem = document.createElement("div");
    // gardien local pour le recalcul des statuts “live”
    eventItem.__itemData = item;
    eventItem.dataset.date = date;                         // YYYY-MM-DD
    eventItem.setAttribute('data-minute', getItemSortMinute(item)); // pour l’auto-scroll
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

    // statut “métier” (base)
    const baseStatus = getPrepStatus(item);
    // statut “runtime” (heure courante)
    const _now = TimelineClock.get();
    const _nowYMD = ymdLocal(_now);
    const _nowMin = _now.getHours()*60 + _now.getMinutes();
    const displayStatus = getRuntimeDisplayStatus(baseStatus, item, date, _nowYMD, _nowMin);

    const prepHtml = displayStatus
    ? `<span class="prep-chip prep-${displayStatus}" title="Préparation : ${getPrepLabel(displayStatus)}">${getPrepLabel(displayStatus)}</span>`
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
        if (!requireIdOrWarn(item)) return;
        if (!Number.isFinite(idx) || !tasks[idx]) return;

        // 1) maj du modèle local
        tasks[idx].done = input.checked;
        item.todo = serializeTodo(tasks);

        const txt = li.querySelector('.todo-text');
        if (txt) txt.classList.toggle('todo-done', input.checked);

        // 2) sauvegarde TODO (toujours)
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

          // 3) statut auto (ready / progress)
          const doneCount = tasks.filter(t => t.done).length;
          const allDone   = tasks.length > 0 && doneCount === tasks.length;
          const newStatus = allDone ? "true" : (tasks.length ? "progress" : "");

          // rien à faire si inchangé
          if ((item.preparation_checked || "").toLowerCase() === newStatus) {
            applyPreparationStatus(eventItem, newStatus || 'none');
            return;
          }

          // a) prêt → passe par /set_preparation_ready si tu l’as
          if (allDone) {
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
            }).then(() => {
              item.preparation_checked = "true";
              applyPreparationStatus(eventItem, "true");
            }).catch(()=>{});
            return;
          }

          // b) non prêt → “progress” (et on persiste)
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
              preparation_checked: newStatus,  // "progress" ou ""
              todo: item.todo
            })
          }).then(() => {
            item.preparation_checked = newStatus;
            applyPreparationStatus(eventItem, newStatus || 'none');
          }).catch(()=>{});
        })
        .catch(err => console.error("Save TODO failed:", err));
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

  // ✅ statut runtime au moment du rendu
  const _now = TimelineClock.get();
  const _nowYMD = ymdLocal(_now);
  const _nowMin = _now.getHours()*60 + _now.getMinutes();
  const clusterDisp = getClusterDisplayStatus(cluster, date, _nowYMD, _nowMin);

  const clusterPrepHtml = clusterDisp
    ? `<span class="prep-chip prep-${clusterDisp}" title="Préparation : ${getPrepLabel(clusterDisp)}">${getPrepLabel(clusterDisp)}</span>`
    : "";

  const el = document.createElement('div');
  el.classList.add('event-item');
  el.setAttribute('data-minute', getClusterSortMinute(cluster));
  el.__clusterData = { cluster, date }; // 👈 pour les recalculs live

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
          // chip individuel (base métier, pas besoin runtime ici dans la sous-ligne)
          const s = getPrepStatus(ch);
          const chip = s ? `<span class="prep-chip prep-${s} sm" title="Préparation : ${getPrepLabel(s)}">${getPrepLabel(s)}</span>` : '';
          return `
            <li class="cluster-line" data-child-id="${ch._id}" style="display:flex; gap:8px; align-items:center; padding:6px 0; border-bottom:1px solid rgba(255,255,255,0.08); cursor:pointer;">
              <span class="material-icons" style="font-size:18px; opacity:.8;">chevron_right</span>
              <div style="flex:1; display:flex; align-items:center; gap:8px;">
                <div style="flex:1;">
                  <div style="font-weight:700;">${title}</div>
                  <div style="opacity:.8; font-size:12px;">${place} ${hours?('• '+hours):''}</div>
                </div>
                ${chip}
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
               removeRedundantOpenClosePairs(data.data, { mode: 'all' });

                Object.keys(data.data).sort().forEach(date => {
                    const items = data.data[date];

                    logDupesOnce(items, date);

                    const dateSection = document.createElement("div");
                    dateSection.classList.add("timetable-date-section");
                    dateSection.dataset.date = date;  
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

    // s'assurer que le champ hidden existe et réinitialiser pour une création
    let prepHidden = addEventForm.querySelector('#prep-status-hidden');
    if (!prepHidden) {
      prepHidden = document.createElement('input');
      prepHidden.type = 'hidden';
      prepHidden.name = 'preparation_checked';
      prepHidden.id = 'prep-status-hidden';
      addEventForm.appendChild(prepHidden);
    }
    prepHidden.value = ''; // pas de statut imposé à la création

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
    addEventButton.addEventListener('click', async function(){
      const cats = await loadCategories();
      categorySelect.innerHTML = '';
      (cats || []).forEach(cat => {
        const option = document.createElement('option');
        option.value = cat;
        option.textContent = cat;
        categorySelect.appendChild(option);
      });
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

        // Validation personnalisée: au moins start OU end
        const startVal = (document.getElementById('start-time').value || '').trim();
        const endVal   = (document.getElementById('end-time').value   || '').trim();

        // Nettoyage anciens messages d'erreur éventuels
        document.querySelectorAll('.form-error').forEach(n => n.remove());

        if (!startVal && !endVal) {
          const err = document.createElement('div');
          err.className = 'form-error';
          err.textContent = 'Saisir au moins une heure de début ou de fin.';
          // on affiche l’erreur sous le champ "Heure de début"
          document.getElementById('start-time').closest('.form-group').appendChild(err);
          document.getElementById('start-time').focus();
          return; // stop submit
        }

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
            origin: isEdit ? "manual-edit" : "manual",
            preparation_checked: (document.getElementById('prep-status-hidden')?.value ?? '')
        };

        // IDs pour décider si on UPDATE ou si on ADD
        const editId      = (document.getElementById('edit-id-hidden')?.value || '').trim();
        const editParamId = (document.getElementById('edit-param-hidden')?.value || '').trim();

        // Ajouter les IDs au payload en mode édition
        if (isEdit) {
          if (editId)      payload._id = editId;
        }

        // Sécuriser l’endpoint : si on est en "edit" mais qu'on n'a ni _id ni param_id, on bascule en création
        let finalEndpoint = endpoint;
        if (isEdit && !editId) {
          console.warn('[Timetable] Edit sans _id/param_id -> fallback création');
          finalEndpoint   = '/add_timetable_event';
          payload.origin  = 'manual';
        }

        // (facultatif mais utile)
        console.debug('[Timetable submit]', { isEdit, finalEndpoint, payload });

        fetch(finalEndpoint, {
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

function maybePromoteReadyFromTodos(dateStr, item, containerEl) {
  const tasks = splitTodo(item.todo || "");
  const allDone = tasks.length > 0 && tasks.every(t => t.done);

  // si pas tout coché → "progress" (ou "none" s'il n'y a plus de tâches) sauf si déjà "true"
  if (!allDone) {
    if ((item.preparation_checked || "").toLowerCase() !== "true") {
      item.preparation_checked = tasks.length ? "progress" : "";
      const pill = containerEl?.querySelector('.prep-pill');
      if (pill) {
        pill.className = `prep-pill prep-${tasks.length ? 'progress' : 'none'}`;
        pill.textContent = tasks.length ? 'En cours' : 'Non';
      }
    }
    return;
  }

  // tout coché → "true"
  if ((item.preparation_checked || "").toLowerCase() === "true") return;

  item.preparation_checked = "true";
  const pill = containerEl?.querySelector('.prep-pill');
  if (pill) { pill.className = 'prep-pill prep-true'; pill.textContent = 'Prête'; }
  if (!requireIdOrWarn(item)) return;

  // (optionnel) notifie le serveur si la route existe
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
      date: dateStr
    })
  }).catch(()=>{});
}

/* --- Vue en lecture --- */
function renderDrawerView() {
  const it = _drawerCurrent.item;
  drawerTitleEl.textContent = it.activity || 'Événement';
  const todoArray = splitTodo(it.todo || "");
  const prep = (it.preparation_checked ?? "").toString().toLowerCase();
  const forceReady = prep === 'true';

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
            const done  = forceReady ? true : !!line.done;
            const clean = line.text;
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

  // Wiring TODO interactions (version robuste + autosave)
  drawerBodyEl.querySelectorAll('input[type="checkbox"][data-idx]').forEach(cb => {
    cb.addEventListener('change', () => {
      const idx  = Number(cb.dataset.idx);
      const list = splitTodo(_drawerCurrent.item.todo || "");
      if (!Number.isFinite(idx) || !list[idx]) return;

      list[idx].done = cb.checked;
      _drawerCurrent.item.todo = serializeTodo(list);

      // statut auto (ready / progress) côté drawer
      const doneCount = list.filter(t => t.done).length;
      const allDone   = list.length > 0 && doneCount === list.length;
      const newStatus = allDone ? "true" : (list.length ? "progress" : "");

      if ((_drawerCurrent.item.preparation_checked || "").toLowerCase() !== newStatus) {
        _drawerCurrent.item.preparation_checked = newStatus;

        // maj visuelle immédiate
        const pill = drawerBodyEl.querySelector('.prep-pill');
        if (pill) {
          pill.className = `prep-pill prep-${newStatus || 'none'}`;
          pill.textContent = allDone ? 'Prête' : (list.length ? 'En cours' : 'Non');
        }
      }

      // 🔸 AUTOSAVE pour la déco / coche
      saveUpdate(_drawerCurrent.date, _drawerCurrent.item);
    });
  });

  const addLine   = drawerBodyEl.querySelector('#todo-add-line');
  const addFirst  = drawerBodyEl.querySelector('#todo-add-first');
  const clearDone = drawerBodyEl.querySelector('#todo-clear-done');
  const saveTodo  = drawerBodyEl.querySelector('#todo-save');

  addLine?.addEventListener('click', () => {
    const list = splitTodo(_drawerCurrent.item.todo || "");
    const txt = prompt('Nouvelle tâche :');
    if (txt && txt.trim()) {
      list.push({ text: txt.trim(), done: false });
      _drawerCurrent.item.todo = serializeTodo(list);
      renderDrawerView(); // re-render
    }
  });

  addFirst?.addEventListener('click', () => {
    const list = splitTodo(_drawerCurrent.item.todo || "");
    const txt = prompt('Nouvelle tâche :');
    if (txt && txt.trim()) {
      list.push({ text: txt.trim(), done: false });
      _drawerCurrent.item.todo = serializeTodo(list);
      renderDrawerView();
    }
  });

  clearDone?.addEventListener('click', () => {
    const list = splitTodo(_drawerCurrent.item.todo || "").filter(l => !l.done);
    _drawerCurrent.item.todo = serializeTodo(list);
    renderDrawerView();
  });

  // bouton "Enregistrer TODO" manuel (au cas où)
  saveTodo?.addEventListener('click', () => {
    saveUpdate(_drawerCurrent.date, _drawerCurrent.item);
  });
}

/* --- Boutons pied de drawer --- */
btnEdit?.addEventListener('click', async () => {
  if (!_drawerCurrent?.item) return;
  await openEditModalFromDrawer(_drawerCurrent.date, _drawerCurrent.item);
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
  if (!requireIdOrWarn(item)) return;
  const payload = {
    event:      window.selectedEvent,
    year:       window.selectedYear,
    date:       dateStr,
    _id:        item._id,
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
  if (!requireIdOrWarn(item)) return;
  if (!it || !it._id) return;
  if (!confirm("Supprimer définitivement cet événement ?")) return;

  const payload = {
    event: window.selectedEvent,
    year:  window.selectedYear,
    date:  _drawerCurrent.date,
    _id:   it._id,
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

// ---- Chargement (et cache) des catégories ----
async function loadCategories() {
  try {
    if (Array.isArray(window.categories) && window.categories.length) {
      return window.categories; // cache
    }
    if (!window.selectedEvent || !window.selectedYear) {
      console.warn('[loadCategories] selectedEvent/year manquants');
      window.categories = [];
      return window.categories;
    }
    const url = '/get_timetable_categories?event=' +
      encodeURIComponent(window.selectedEvent) +
      '&year=' + encodeURIComponent(window.selectedYear);

    const res = await fetch(url);
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const data = await res.json();
    window.categories = Array.isArray(data?.categories) ? data.categories : [];
    return window.categories;
  } catch (e) {
    console.error('[loadCategories] échec:', e);
    window.categories = window.categories || [];
    return window.categories;
  }
}

async function openEditModalFromDrawer(dateStr, item) {
  // 1) garantir qu'on a les catégories
  const cats = await loadCategories().catch(()=>[]);

  // 2) références
  const addEventModal  = document.getElementById('addEventModal');
  const addEventForm   = document.getElementById('addEventForm');
  const titleEl        = addEventModal?.querySelector('h3');

  // 3) passer en mode édition (utilisé par ton submit)
  window.formMode = 'edit';
  window.editingItemId = item._id || null;
  if (titleEl) titleEl.textContent = "Modifier un événement de la Timetable";

  // 4) remplir le select catégories
  const categorySelect = document.getElementById('category');
  if (categorySelect) {
    categorySelect.innerHTML = '';
    const source = (Array.isArray(cats) && cats.length) ? cats : (item.category ? [item.category] : []);
    source.forEach(cat => {
      const opt = document.createElement('option');
      opt.value = cat;
      opt.textContent = cat;
      if (cat === item.category) opt.selected = true;
      categorySelect.appendChild(opt);
    });
  }

  // 5) pré-remplir tous les champs du formulaire
  const setVal = (id, v) => { const el = document.getElementById(id); if (el) el.value = v ?? ''; };
  setVal('event-date',   dateStr || '');
  setVal('start-time',   (item.start && item.start!=='TBC') ? item.start : '');
  setVal('end-time',     (item.end   && item.end  !=='TBC') ? item.end   : '');
  setVal('duration',     item.duration || '');
  setVal('activity',     item.activity || '');
  setVal('place',        item.place || '');
  setVal('department',   item.department || '');
  setVal('remark',       item.remark || '');

  // 6) hidden _id (ID FIXE pour éviter toute ambiguïté)
  let hidden = addEventForm.querySelector('#edit-id-hidden');
  if (!hidden) {
    hidden = document.createElement('input');
    hidden.type = 'hidden';
    hidden.name = '_id';
    hidden.id   = 'edit-id-hidden';
    addEventForm.appendChild(hidden);
  }
  hidden.value = item._id || '';

   // hidden pour conserver le statut de préparation pendant l'édition
  let prepHidden = addEventForm.querySelector('input[name="preparation_checked"]');
  if (!prepHidden) {
    prepHidden = document.createElement('input');
    prepHidden.type = 'hidden';
    prepHidden.name = 'preparation_checked';
    prepHidden.id = 'prep-status-hidden';
    addEventForm.appendChild(prepHidden);
  }
  prepHidden.value = (item.preparation_checked ?? '').toString().toLowerCase();

  // 7) fermer le drawer AVANT d’ouvrir la modale (évite le warning ARIA)
  closeEventDrawer();
  // enlever le focus actuel pour ne pas "cacher" un élément focusable
  document.activeElement && document.activeElement.blur?.();

  // 8) ouvrir la modale
  const openModal = (modal)=>{
    modal.style.display='block';
    setTimeout(()=>modal.classList.add('show'),10);
  };
  openModal(addEventModal);
}

/******************************************************************
 * Horloge simulable (console) + ligne rouge + auto-scroll
 ******************************************************************/
(function(){
  // ---------- Horloge simulable ----------
  const TimelineClock = {
    _mode: 'real',          // 'real' | 'sim'
    _simDate: null,         // Date simulée
    _playing: false,
    _speed: 1,              // minutes simulées / seconde réelle
    _timer: null,
    _lastTs: 0,

    useReal(){
      this._mode = 'real';
      this._simDate = null;
      this.pause();
      console.info('[Clock] mode=real');
    },
    setSim(d){
      let dt = null;

      if (typeof d === 'string') {
        // Accepte 1 ou 2 chiffres pour mois/jour, et HH:MM (obligatoire ici)
        const m = d.match(/^(\d{4})-(\d{1,2})-(\d{1,2})[ T](\d{1,2}):(\d{2})$/);
        if (m) {
          const Y  = Number(m[1]);
          const Mo = Number(m[2]); // 1..12
          const D  = Number(m[3]); // 1..31
          const H  = Number(m[4]); // 0..23
          const Mi = Number(m[5]); // 0..59
          if ([Y,Mo,D,H,Mi].some(n => !Number.isFinite(n))) {
            console.warn('[Clock] setSim: composant non numérique'); return;
          }
          // Date en FUSEAU LOCAL (important pour éviter l’effet UTC)
          dt = new Date(Y, Mo - 1, D, H, Mi, 0, 0);
        } else {
          // Fallback natif (toujours local si string sans Z)
          const parsed = new Date(d);
          if (isNaN(+parsed)) { console.warn('[Clock] string invalide'); return; }
          dt = parsed;
        }
      } else if (d instanceof Date) {
        // Clone défensif
        dt = new Date(d.getTime());
      } else {
        console.warn('[Clock] setSim attend "YYYY-MM-DD HH:MM" ou Date');
        return;
      }

      this._mode = 'sim';
      this._simDate = dt;
      console.info('[Clock] mode=sim', this._simDate.toString());
    },
    setSpeed(minPerSec){
      const v = Number(minPerSec);
      if (!isFinite(v) || v <= 0) { console.warn('[Clock] vitesse invalide'); return; }
      this._speed = v;
      console.info('[Clock] speed =', this._speed, 'min/s');
    },
    play(){
      if (this._mode !== 'sim') { console.warn('[Clock] play: passe d’abord en mode simulé avec setSim'); return; }
      if (this._playing) return;
      this._playing = true;
      this._lastTs = performance.now();
      this._timer = setInterval(()=>{
        const now = performance.now();
        const dtMs = now - this._lastTs;
        this._lastTs = now;
        // Avance en minutes simulées:
        const advanceMin = (dtMs/1000) * this._speed;
        this._simDate = new Date(this._simDate.getTime() + advanceMin*60*1000);
      }, 200); // 5 ticks/sec pour un rendu fluide
      console.info('[Clock] ▶ play');
    },
    pause(){
      if (!this._playing) return;
      clearInterval(this._timer);
      this._timer = null;
      this._playing = false;
      console.info('[Clock] ❚❚ pause');
    },
    step(minutes=1){
      if (this._mode !== 'sim' || !this._simDate) return;
      const min = Number(minutes) || 1;
      this._simDate = new Date(this._simDate.getTime() + min*60*1000);
    },
    get(){
      return (this._mode === 'sim' && this._simDate)
        ? new Date(this._simDate.getTime())
        : new Date();
    }
  };
  window.TimelineClock = TimelineClock; // API console

  // --- Helpers mapping temps → position verticale ---
  const _anchorsCache = new WeakMap(); // sectionEl -> {anchors, stamp}

  function _buildTimeAnchorsForSection(sectionEl){
    const items = Array.from(sectionEl.querySelectorAll('.event-item'));
    const raw = [];
    for (const el of items) {
      const m = Number(el.getAttribute('data-minute'));
      if (!isFinite(m)) continue;     // ignore TBC/Infinity
      raw.push({ minute: m, y: el.offsetTop }); // y relatif au haut de la section
    }
    if (!raw.length) return [];

    // Regroupement par minute identique → moyenne de y (stabilise l’interpolation)
    const byMin = new Map();
    for (const r of raw) {
      const arr = byMin.get(r.minute) || [];
      arr.push(r.y);
      byMin.set(r.minute, arr);
    }
    const anchors = Array.from(byMin.entries())
      .map(([minute, ys]) => ({ minute, y: ys.reduce((a,b)=>a+b,0)/ys.length }))
      .sort((a,b)=> a.minute - b.minute);

    return anchors;
  }

  function _getAnchors(sectionEl){
    const stamp = sectionEl.scrollHeight + '|' + sectionEl.childElementCount;
    const cached = _anchorsCache.get(sectionEl);
    if (cached && cached.stamp === stamp) return cached.anchors;
    const anchors = _buildTimeAnchorsForSection(sectionEl);
    _anchorsCache.set(sectionEl, { anchors, stamp });
    return anchors;
  }

  function _minuteToY(minute, anchors){
    if (!anchors || anchors.length === 0) return 0;
    if (anchors.length === 1) return anchors[0].y;

    // Avant la première ancre: extrapole avec la première pente
    if (minute <= anchors[0].minute) {
      const a = anchors[0], b = anchors[1];
      const slope = (b.y - a.y) / (b.minute - a.minute || 1);
      return a.y + (minute - a.minute)*slope;
    }
    // Après la dernière ancre: extrapole avec la dernière pente
    if (minute >= anchors[anchors.length-1].minute) {
      const a = anchors[anchors.length-2], b = anchors[anchors.length-1];
      const slope = (b.y - a.y) / (b.minute - a.minute || 1);
      return b.y + (minute - b.minute)*slope;
    }

    // Entre deux ancres: interpolation linéaire
    for (let i=0;i<anchors.length-1;i++){
      const a = anchors[i], b = anchors[i+1];
      if (minute >= a.minute && minute <= b.minute) {
        const t = (minute - a.minute) / (b.minute - a.minute || 1);
        return a.y + t*(b.y - a.y);
      }
    }
    return anchors[anchors.length-1].y;
  }

  function fmtHHMM(d){
    const h = String(d.getHours()).padStart(2,'0');
    const m = String(d.getMinutes()).padStart(2,'0');
    return `${h}:${m}`;
  }

  // ---------- Ligne rouge + auto-scroll ----------
  const NowLineController = {
    _enabled: false,
    _timer: null,
    _intervalMs: 15000,  // recalage périodique
    _lineTopPx: 100,     // top CSS de #now-line
    _lineEl: null,

    init(){
      // s’assure que la ligne existe bien DANS #timeline-main
      const main = document.getElementById('timeline-main');
      let el = document.getElementById('now-line');

      if (!main) { console.warn('[NowLine] timeline-main introuvable'); return; }

      if (!el) {
        el = document.createElement('div');
        el.id = 'now-line';
        el.hidden = true;
        el.innerHTML = '<span class="now-line-label">--:--</span>';
        main.prepend(el);
      } else if (el.parentElement !== main) {
        // si la ligne était ailleurs, on la déplace
        el.remove();
        main.prepend(el);
      }

      this._lineEl = el;

      // applique le top initial en pixels (pilote la “hauteur” apparente de la ligne)
      this._lineEl.style.top = `${this._lineTopPx}px`;
    },
    setInterval(ms){
      const v = Number(ms);
      if (!isFinite(v) || v < 100) return;
      this._intervalMs = v;
      if (this._enabled) { clearInterval(this._timer); this._timer = setInterval(()=>this._tick(), this._intervalMs); }
    },
    setLineTop(px){
      const v = Number(px);
      if (!isFinite(v) || v < 0) return;
      this._lineTopPx = v;
    },
    start(){
      if (this._enabled) return;
      this._enabled = true;
      this._lineEl.hidden = false;
      this._tick(); // immédiat
      this._timer = setInterval(()=>this._tick(), this._intervalMs);
      console.info('[NowLine] auto-scroll ON');
    },
    stop(){
      if (!this._enabled) return;
      this._enabled = false;
      this._lineEl.hidden = true;
      if (this._timer) clearInterval(this._timer);
      this._timer = null;
      console.info('[NowLine] auto-scroll OFF');
    },
    toggle(){ this._enabled ? this.stop() : this.start(); },

    _tick(){
      const container = document.querySelector('.timeline-container');
      if (!container || !this._lineEl) return;

      // --- Sections triées avec leur date ISO ---
      const sections = Array.from(container.querySelectorAll('.timetable-date-section'));
      if (!sections.length) return;

      const map = sections
        .map(sec => ({ el: sec, iso: sec.dataset.date || null }))
        .filter(x => !!x.iso)
        .sort((a,b) => a.iso.localeCompare(b.iso));
      if (!map.length) return;

      // --- Heure courante (réelle/simulée) + libellé ---
      const now    = TimelineClock.get();
      const nowYMD = ymdLocal(now);
      const nowMin = now.getHours()*60 + now.getMinutes();
      const hh = String(now.getHours()).padStart(2,'0');
      const mm = String(now.getMinutes()).padStart(2,'0');

      const labelEl = this._lineEl.querySelector('.now-badge') || this._lineEl.querySelector('.now-line-label');
      if (labelEl) labelEl.textContent = `${hh}:${mm}`;

      // --- Section cible : aujourd’hui > dernière passée > première future ---
      let target = map.find(x => x.iso === nowYMD);
      if (!target) {
        const past = map.filter(x => x.iso < nowYMD);
        if (past.length) target = past[past.length - 1];
      }
      if (!target) target = map[0];

      const targetSection = target.el;
      const targetISO     = target.iso;

      // --- Cartes triées par minute (ignore Infinity/TBC) ---
      const cards = Array.from(targetSection.querySelectorAll('.event-item'))
        .map(el => ({ el, minute: parseInt(el.getAttribute('data-minute') || '999999', 10) }))
        .filter(x => Number.isFinite(x.minute))
        .sort((a,b) => a.minute - b.minute);

      // --- Géométrie du container ---
      const contRect = container.getBoundingClientRect();
      const currentScroll = container.scrollTop;
      const maxScroll = container.scrollHeight - container.clientHeight;

      // util: poser la ligne à une position Y *dans le viewport du container*
      const placeLineViewportY = (y) => {
        let lineTop = Math.max(0, Math.min(container.clientHeight - 2, y));
        this._lineEl.style.top = `${lineTop}px`;
      };

      // --- Cas sans carte exploitable : caler sur le haut de section
      if (!cards.length) {
        const secRect = targetSection.getBoundingClientRect();
        const sectionAbsTop = currentScroll + (secRect.top - contRect.top);

        const desiredScrollTop = Math.max(0, sectionAbsTop - this._lineTopPx);
        const clamped = Math.max(0, Math.min(desiredScrollTop, maxScroll));
        container.scrollTo({ top: clamped, behavior: 'smooth' });

        if (clamped !== desiredScrollTop) {
          // butée → place la ligne sur le haut de section visible
          const sectionTopViewportY = secRect.top - contRect.top;
          placeLineViewportY(sectionTopViewportY);
        } else {
          // position standard
          placeLineViewportY(this._lineTopPx);
        }
        console.info('[NowLine] fallback: section sans heures valides', targetISO);
        return;
      }

      // --- Minute cible dans la section ---
      let minuteTarget;
      if (targetISO === nowYMD) {
        const next = cards.find(c => c.minute >= nowMin);
        minuteTarget = next ? next.minute : cards[cards.length-1].minute;
      } else if (targetISO < nowYMD) {
        minuteTarget = cards[cards.length-1].minute; // fin de journée passée
      } else {
        minuteTarget = cards[0].minute;              // début de journée future
      }

      // --- Carte pivot ---
      const pivot = cards.find(c => c.minute >= minuteTarget) || cards[cards.length-1];

      // --- Position absolue du top de la carte pivot ---
      const cardRect = pivot.el.getBoundingClientRect();
      const pivotAbsTop = currentScroll + (cardRect.top - contRect.top);

      // On veut mettre le top de la carte à _lineTopPx
      const desiredScrollTop = Math.max(0, pivotAbsTop - this._lineTopPx);
      const clamped = Math.max(0, Math.min(desiredScrollTop, maxScroll));
      container.scrollTo({ top: clamped, behavior: 'smooth' });

      // --- Gestion des butées ---
      if (clamped !== desiredScrollTop) {
        const bottomSafe = 8;   // marge au-dessus du bas visible
        const topSafe    = 8;   // marge sous le haut visible

        if (clamped === maxScroll) {
          // ➜ Butée BAS : ligne au bas de la DERNIÈRE carte
          const lastCard = cards[cards.length - 1].el;
          const lastRect = lastCard.getBoundingClientRect();
          const lastBottomViewportY = lastRect.bottom - contRect.top;
          const y = Math.min(container.clientHeight - bottomSafe, lastBottomViewportY);
          placeLineViewportY(y);
        } else if (clamped === 0) {
          // ➜ Butée HAUT : ligne au haut de la PREMIÈRE carte
          const firstCard = cards[0].el;
          const firstRect = firstCard.getBoundingClientRect();
          const firstTopViewportY = firstRect.top - contRect.top;
          const y = Math.max(topSafe, firstTopViewportY);
          placeLineViewportY(y);
        } else {
          // Sécurité : coller à la carte pivot
          const pivotTopViewportY = cardRect.top - contRect.top;
          placeLineViewportY(pivotTopViewportY);
        }
      } else {
        // Pas de butée → position standard
        placeLineViewportY(this._lineTopPx);
      }

      console.info('[NowLine] tick', {
        section: targetISO,
        minuteTarget,
        pivotMinute: pivot.minute,
        clampedTo: clamped,
        maxScroll
      });

      // 🔁 Recalcule les statuts dynamiques sur TOUTES les cartes visibles (items + clusters)
      try {
        const allCards = Array.from(document.querySelectorAll('.timetable-date-section .event-item'));
        for (const card of allCards) {
          const cardDate = card.closest('.timetable-date-section')?.dataset?.date || null;
          if (!cardDate) continue;

          let chip = card.querySelector('.prep-chip');
          if (!chip) continue;

          // Cas 1 : carte d'ITEM individuel (créée via createEventItem)
          if (card.__itemData) {
            const item = card.__itemData;
            const base = getPrepStatus(item) || 'none';
            const disp = getRuntimeDisplayStatus(base, item, cardDate, nowYMD, nowMin);
            chip.className = `prep-chip prep-${disp}`;
            chip.textContent = getPrepLabel(disp);
            continue;
          }

          // Cas 2 : carte de CLUSTER (créée via createClusterItem)
          if (card.__clusterData) {
            const { cluster, date } = card.__clusterData;
            const disp = getClusterDisplayStatus(cluster, date || cardDate, nowYMD, nowMin);
            if (disp) {
              chip.className = `prep-chip prep-${disp}`;
              chip.textContent = getPrepLabel(disp);
            }
          }
        }
      } catch(e) { console.warn('Refresh runtime statuses failed', e); }
    }
  };
  window.NowLineController = NowLineController;
  NowLineController.init();

  // ---------- Bouton UI ----------
  document.addEventListener('DOMContentLoaded', () => {
    const btn = document.getElementById('nowline-toggle');
    if (btn) {
      btn.addEventListener('click', () => {
        NowLineController.toggle();
        btn.title = NowLineController._enabled ? 'Auto-scroll arrêt' : 'Auto-scroll maintenant';
        const icon = btn.querySelector('.material-symbols-outlined');
        if (icon) icon.textContent = NowLineController._enabled ? 'pause_circle' : 'schedule';
      });
    }
  });

  // ---------- Recalage après rendu de la timeline ----------
  // Appelé après chaque fetchTimetable() (en douceur, pour éviter les race conditions)
  const _origFetch = window.fetchTimetable;
  if (typeof _origFetch === 'function') {
    window.fetchTimetable = function() {
      const p = _origFetch.apply(this, arguments);
      Promise.resolve(p).then(()=>{
        // petit délai pour que le DOM soit prêt
        setTimeout(()=> { NowLineController._enabled && NowLineController._tick(); }, 60);
      });
      return p;
    };
  }
})();