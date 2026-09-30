/////////////////////////////////////////////////////////////////////////////////////////////////////
// HELPERS
/////////////////////////////////////////////////////////////////////////////////////////////////////

// Fonction utilitaire pour convertir l'heure en minutes depuis minuit.
// Accepte "HH:MM" et "HHhMM" (format FR utilise par certaines categories, ex: Procedures).
function timeToMinutes(timeStr) {
    if (!timeStr || timeStr.toUpperCase() === "TBC") return Infinity;
    const m = String(timeStr).trim().match(/^(\d{1,2})[:h](\d{2})$/i);
    if (!m) return Infinity;
    const h = parseInt(m[1], 10);
    const mn = parseInt(m[2], 10);
    if (isNaN(h) || isNaN(mn)) return Infinity;
    return h * 60 + mn;
}

// Normalise une heure pour l'affichage: "15h30" -> "15:30", "9h05" -> "09:05".
// Renvoie la valeur brute si elle ne match aucun format reconnu (TBC, vide, autre).
function formatHHMM(s) {
    if (!s) return s;
    const t = String(s).trim();
    if (!t || t.toUpperCase() === 'TBC') return t;
    const m = t.match(/^(\d{1,2})[:h](\d{2})$/i);
    if (!m) return t;
    return `${m[1].padStart(2,'0')}:${m[2]}`;
}

// Fonction pour tronquer une chaîne à un nombre max de caractères
function truncateText(text, maxChars) {
    return text.length > maxChars ? text.substring(0, maxChars) + "…" : text;
}

// Pillule de categorie : slug deterministe + fallback hashe pour categories inconnues
function _categorySlug(cat) {
    return String(cat || '')
        .normalize('NFD').replace(/[\u0300-\u036f]/g, '')
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, '-')
        .replace(/^-+|-+$/g, '');
}
function _categoryFallbackIdx(slug) {
    let h = 0;
    for (let i = 0; i < slug.length; i++) { h = ((h << 5) - h) + slug.charCodeAt(i); h |= 0; }
    return (Math.abs(h) % 10) + 1; // 1..10
}
const _CAT_KNOWN = new Set([
    'parking','controle','aa','hospi','general','motos','evenement',
    'pco-secours','pco-securite','secours','securite'
]);
function getCategoryChipHtml(cat) {
    const label = String(cat || '').trim();
    if (!label) return '';
    const slug = _categorySlug(label);
    const cls = _CAT_KNOWN.has(slug)
        ? `cat-${slug}`
        : `cat-fb cat-fb-${_categoryFallbackIdx(slug)}`;
    return `<span class="cat-chip ${cls}" title="Categorie : ${label}">${label}</span>`;
}

// 🔹 Mode d'acces d'un item ('controle'|'libre') a partir de item.dayControl,
// porte par les vignettes des categories portes/parkings/aires/tribunes/
// boutiques/campements. Retourne null si non renseigne.
function getAccessInfo(item) {
    const mode = String((item && item.dayControl) || '').toLowerCase();
    if (mode === 'controle') return { mode, label: 'Contrôlé',    icon: 'shield_lock',      cls: 'access-controle' };
    if (mode === 'libre')    return { mode, label: 'Accès libre', icon: 'lock_open_right',   cls: 'access-libre' };
    return null;
}

// 🔹 Chip d'acces a l'ouverture. Limite a la phase 'open' (a la fermeture
// l'info n'a pas de sens — la vignette de fermeture n'ouvre rien).
function getAccessChipHtml(item) {
    if (!item || item.phase !== 'open') return '';
    const info = getAccessInfo(item);
    if (!info) return '';
    return `<span class="access-chip ${info.cls}" title="Ouverture en acces ${info.mode}">`
         + `<span class="material-symbols-outlined">${info.icon}</span>${info.label}</span>`;
}

// 🔹 Construit un index { 'YYYY-MM-DD': { is24h, openTime, closeTime } }
function buildPublicDatesMap(parametrage) {
  const map = {};
  const dates = parametrage?.globalHoraires?.dates || parametrage?.data?.globalHoraires?.dates || [];
  dates.forEach(d => {
    if (d?.date) {
      map[d.date] = {
        is24h: !!d.is24h || (d.openTime === "00:00" && (d.closeTime === "23:59" || d.closeTime === "24:00" || d.closeTime === "00:00")),
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
  // Détection de continuité avec un voisin :
  // si J ouvre à 00:00 ET J-1 ferme à 23:59/24:00 → continuité nocturne, le 00:00 est artificiel
  // si J ferme à 23:59/24:00 ET J+1 ouvre à 00:00 → continuité nocturne, la fermeture est artificielle
  const d = new Date(dateStr + 'T00:00:00');
  const prev = new Date(d); prev.setDate(prev.getDate() - 1);
  const next = new Date(d); next.setDate(next.getDate() + 1);
  // Bug timezone : toISOString() decale en UTC, on utilise un format local
  const prevEntry = window.publicDatesMap?.[ymdLocal(prev)];
  const nextEntry = window.publicDatesMap?.[ymdLocal(next)];
  const openIs00 = entry.openTime === "00:00";
  const closeIs2359 = entry.closeTime === "23:59" || entry.closeTime === "24:00" || entry.closeTime === "00:00";
  const prevCloseIs2359 = prevEntry && (prevEntry.closeTime === "23:59" || prevEntry.closeTime === "24:00" || prevEntry.closeTime === "00:00");
  const nextOpenIs00 = nextEntry && nextEntry.openTime === "00:00";
  const continuesFromPrev = openIs00 && prevCloseIs2359;
  const continuesToNext = closeIs2359 && nextOpenIs00;

  if (continuesFromPrev && continuesToNext) {
    return { text: "OUVERT AU PUBLIC — 24/24", className: "banner-open" };
  }
  if (continuesFromPrev) {
    return { text: `OUVERT AU PUBLIC — jusqu'à ${entry.closeTime}`, className: "banner-open" };
  }
  if (continuesToNext) {
    return { text: `OUVERT AU PUBLIC — à partir de ${entry.openTime}`, className: "banner-open" };
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

  // ⬇️ si prêt -> on force l'affichage comme coché
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

// --- Helpers clustering (dynamique via /api/cluster-config) ---
function norm(s){ return (s||'').toString().toLowerCase().normalize('NFD').replace(/\p{Diacritic}/gu,''); }

// CLUSTER_CONFIG est construit dynamiquement depuis la DB.
// Chaque entree: { label, icon, match(item)->bool }
// Le matching se fait par activity_label (pattern du merge_config).
let CLUSTER_CONFIG = {};
let _clusterConfigLoaded = false;

function _buildMatcherFromLabel(activityLabel) {
  // activity_label = "Parking {name}" -> on matche si activity contient "Parking "
  // ou "Ouverture Parking " ou "Fermeture Parking "
  const prefix = (activityLabel || "").split(/\s*\{[^}]+\}/)[0].trim();
  if (!prefix) return () => false;
  const prefixNorm = norm(prefix);
  return function(it) {
    const a = norm(it.activity || "");
    return a.includes(prefixNorm);
  };
}

async function loadClusterConfig() {
  try {
    const res = await fetch("/api/cluster-config");
    if (!res.ok) return;
    const configs = await res.json();
    const built = {};
    configs.forEach(cfg => {
      const key = cfg.data_key;
      built[key] = {
        label: cfg.label || key,
        icon: cfg.cluster_icon || "category",
        match: _buildMatcherFromLabel(cfg.activity_label),
      };
    });
    CLUSTER_CONFIG = built;
    _clusterConfigLoaded = true;
  } catch (e) {
    console.warn("Impossible de charger cluster-config:", e);
  }
}

// Charger au demarrage (la promesse est reutilisee par fetchTimetable si besoin)
const _clusterConfigReady = loadClusterConfig();

function detectClusterType(it) {
  for (const [key, cfg] of Object.entries(CLUSTER_CONFIG)) {
    if (cfg.match(it)) return key;
  }
  return null;
}

function groupByClusters(items) {
  const rest = [];
  const buckets = {}; // key: `${type}|${kind}|${timeKey}` -> []

  items.forEach(it => {
    const type = detectClusterType(it);
    if (!type) { rest.push(it); return; }

    const kind = getOpenCloseKind(it); // 'open'|'close'|null
    if (!kind) { // si on ne sait pas si c'est une ouverture ou une fermeture, on n'agrège pas
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

function validTimeStr(s){ return !!(s && /^\d{1,2}[:h]\d{2}$/i.test(s.trim())); }

function getOpenCloseKind(it){
  // Priorite au champ explicite phase pose par merge.py
  const ph = (it && it.phase || '').toLowerCase();
  if (ph === 'open' || ph === 'close' ||
      ph === 'switch_control' || ph === 'switch_free') return ph;

  // Fallback heuristique pour les vignettes legacy / manuelles
  const s = norm(`${it.activity||''} ${it.category||''} ${it.place||''}`);
  if (/\bbascule\b.*\bcontrol/.test(s) || /\bcontrole(e)?\b/.test(s) && /\bbascule\b/.test(s)) return 'switch_control';
  if (/\bretour\b.*\blibre\b/.test(s) || /\bbascule\b.*\blibre\b/.test(s)) return 'switch_free';
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
      const type = detectClusterType(it);
      const kind = getOpenCloseKind(it);
      if (!type || (kind !== 'open' && kind !== 'close')) return;
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

  // 2) Cross-day: ça n'a de sens qu'à minuit
  if (mode === 'midnight') {
    for (let i = 0; i < dates.length - 1; i++) {
      const d0 = dates[i], d1 = dates[i+1];
      const a0 = (byDate[d0] || []).filter(it => getOpenCloseKind(it) === 'close' && getClusterTimeKey(it, 'close') === '00:00');
      const a1 = (byDate[d1] || []).filter(it => getOpenCloseKind(it) === 'open'  && getClusterTimeKey(it, 'open')  === '00:00');

      if (!a0.length || !a1.length) continue;

      const mapOpen = new Map();
      a1.forEach(it => {
        const type = detectClusterType(it); if (!type) return;
        const key = `${type}|${normPlace(it.place||'')}`;
        (mapOpen.get(key) || mapOpen.set(key, [])).push(it);
      });

      a0.forEach(itClose => {
        const type = detectClusterType(itClose); if (!type) return;
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
function isValidHHMM(s){ return !!(s && /^\d{1,2}[:h]\d{2}$/i.test(s.trim())); }

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

function hasNoTodos(item) {
  return splitTodo(item?.todo || "").length === 0;
}
function validHHMM(s) { return !!(s && /^\d{1,2}[:h]\d{2}$/i.test(s.trim())); }

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
  // 🆕 Cas "aucune tâche" : on affiche Terminé uniquement si l'échéance est passée
 if (hasNoTodos(item)) {
    // Référence = end si valide, sinon start, sinon Infinity
    let dueMin = Infinity;
    if (validHHMM(item.end))   dueMin = timeToMinutes(item.end);
    else if (validHHMM(item.start)) dueMin = timeToMinutes(item.start);

    // date passée -> terminé, même si heure invalide
    if (cardDateStr < nowYMD) return 'done';
    // même jour -> terminé si l'heure de référence est dépassée
    if (cardDateStr === nowYMD && Number.isFinite(dueMin) && nowMin >= dueMin) return 'done';
    // sinon, on n'affiche rien de spécial (revient au statut de base)
  }

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

// Statut agrégé *runtime* d'un cluster (pire des statuts de ses enfants)
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
  const evId = getEventId(item);
  if (!evId) {
    typeof showDynamicFlashMessage === 'function' &&
      showDynamicFlashMessage("Événement incomplet (id manquant)", "error");
    console.warn('[Timetable] ID manquant pour item:', item);
    return false;
  }
  // normalise en mémoire pour les prochaines fois
  if (item && !item._id && evId) item._id = evId;
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

// --- ID helper unique (tolérant) ---
function getEventId(item){
  // 1) priorités: _id puis id
  let v = item?._id ?? item?.id ?? '';
  if (v != null && v !== '') return String(v);

  // 2) fallback depuis le drawer (on y stocke l'ID à l'ouverture)
  const fromDrawer = (drawerEl?.dataset?.eventId) || '';
  if (fromDrawer) return String(fromDrawer);

  return '';
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
  if (!item._id && item.id) item._id = String(item.id);
    const eventItem = document.createElement("div");
    // gardien local pour le recalcul des statuts “live”
    eventItem.__itemData = item;
    eventItem.dataset.date = date;                         // YYYY-MM-DD
    eventItem.setAttribute('data-minute', getItemSortMinute(item)); // pour l'auto-scroll
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

    // Gestion de l'affichage des heures (normalise "HHhMM" -> "HH:MM")
    let timeInfo = "";
    if (item.start && item.start.trim() !== "" && item.start.toUpperCase() !== "TBC") {
        timeInfo = formatHHMM(item.start);
        if (item.end && item.end.trim() !== "" && item.end.toUpperCase() !== "TBC") {
            timeInfo += " - " + formatHHMM(item.end);
        }
    } else if (item.end && item.end.trim() !== "" && item.end.toUpperCase() !== "TBC") {
        timeInfo = formatHHMM(item.end);
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
                ${getCategoryChipHtml(item.category)}
                ${getAccessChipHtml(item)}
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
            <p><strong>Heure de début :</strong> ${formatHHMM(item.start) || "TBC"}</p>
            <p><strong>Heure de fin :</strong> ${formatHHMM(item.end) || "TBC"}</p>
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

          // a) prêt → passe par /set_preparation_ready si tu l'as
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
  cluster.items?.forEach(ch => { if (!ch._id && ch.id) ch._id = String(ch.id); });
  const cfg = CLUSTER_CONFIG[cluster.type];
  const count = cluster.items.length;
  const _kindLabels = {
    open: 'Ouverture',
    close: 'Fermeture',
    switch_control: 'Bascule controlee',
    switch_free: 'Retour libre'
  };
  const _kindIcons = {
    open: cfg.icon,
    close: cfg.icon,
    switch_control: 'shield_lock',
    switch_free: 'lock_open_right'
  };
  const kindLabel = _kindLabels[cluster.kind] || 'Ouverture';
  const headerIcon = _kindIcons[cluster.kind] || cfg.icon;
  const timeInfo = formatHHMM(cluster.time) || 'TBC';

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
        <span class="material-symbols-outlined">${headerIcon}</span>
        <h5>${cfg.label} — ${kindLabel} ${timeInfo} (${count})</h5>
      </div>
      <div class="event-time">
        <p class="time-info">${timeInfo}</p>
        ${getCategoryChipHtml(cluster.items?.[0]?.category)}
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
          const hours = (ch.start && ch.start!=='TBC' ? formatHHMM(ch.start) : '') +
                        (ch.end   && ch.end  !=='TBC' ? (' - '+formatHHMM(ch.end)) : '');
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
                ${getAccessChipHtml(ch)}
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

  // clic sur une sous-ligne -> ouvrir le drawer de l'item
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

// ---------------------------------------------------------------------------
// Day navigation bar
// ---------------------------------------------------------------------------
let _dayNavObserver = null;
let _dayNavTooltip = null;

const _DAY_NAMES_SHORT = ['Dim', 'Lun', 'Mar', 'Mer', 'Jeu', 'Ven', 'Sam'];

function _getDayNavTooltip() {
  if (!_dayNavTooltip) {
    _dayNavTooltip = document.createElement('div');
    _dayNavTooltip.className = 'day-nav-tooltip';
    document.body.appendChild(_dayNavTooltip);
  }
  return _dayNavTooltip;
}

function _showDayNavTooltip(pill, lines) {
  const tip = _getDayNavTooltip();
  tip.textContent = '';
  lines.forEach(function(line) {
    var row = document.createElement('span');
    row.className = 'tt-row';
    var dot = document.createElement('span');
    dot.className = 'tt-dot ' + line.type;
    row.appendChild(dot);
    var txt = document.createElement('span');
    txt.textContent = line.text;
    row.appendChild(txt);
    tip.appendChild(row);
  });

  var rect = pill.getBoundingClientRect();
  tip.style.left = (rect.left + rect.width / 2) + 'px';
  tip.style.top = (rect.bottom + 6) + 'px';
  tip.style.transform = 'translateX(-50%) translateY(4px)';
  tip.classList.add('visible');
  tip.style.transform = 'translateX(-50%) translateY(0)';
}

function _hideDayNavTooltip() {
  if (_dayNavTooltip) {
    _dayNavTooltip.classList.remove('visible');
  }
}

function _buildDayNav(dates, sectionsByDate) {
  const bar = document.getElementById('day-nav-bar');
  if (!bar) return;
  bar.textContent = '';

  if (!dates.length) {
    bar.hidden = true;
    return;
  }
  bar.hidden = false;

  const pills = {};
  dates.forEach(dateStr => {
    const d = new Date(dateStr + 'T00:00:00');
    const pill = document.createElement('button');
    pill.className = 'day-nav-pill';
    pill.dataset.date = dateStr;

    const dayName = document.createElement('span');
    dayName.className = 'day-name';
    dayName.textContent = _DAY_NAMES_SHORT[d.getDay()];

    const dayNum = document.createElement('span');
    dayNum.className = 'day-num';
    dayNum.textContent = d.getDate();

    pill.appendChild(dayName);
    pill.appendChild(dayNum);

    // Indicateurs (points cote a cote)
    const raceRaw = window.parametrage?.race || window.parametrage?.data?.race || "";
    const raceDate = typeof raceRaw === 'string' ? raceRaw.slice(0, 10) : "";
    const isRace = raceDate === dateStr;
    const isPublic = !!window.publicDatesMap?.[dateStr];

    if (isRace || isPublic) {
      const tooltipLines = [];
      const dotsRow = document.createElement('span');
      dotsRow.className = 'day-indicators';
      if (isPublic) {
        pill.classList.add('day-public');
        const dot = document.createElement('span');
        dot.className = 'day-indicator public';
        dotsRow.appendChild(dot);
        const entry = window.publicDatesMap[dateStr];
        // Detecter continuite 24h avec les jours voisins
        const prevDate = new Date(d); prevDate.setDate(prevDate.getDate() - 1);
        const nextDate = new Date(d); nextDate.setDate(nextDate.getDate() + 1);
        const prevKey = prevDate.toISOString().slice(0, 10);
        const nextKey = nextDate.toISOString().slice(0, 10);
        const prevEntry = window.publicDatesMap?.[prevKey];
        const nextEntry = window.publicDatesMap?.[nextKey];

        const openIs00 = entry.openTime === '00:00';
        const closeIs2359 = entry.closeTime === '23:59' || entry.closeTime === '24:00' || entry.closeTime === '00:00';

        const prevCloseIs2359 = prevEntry && (prevEntry.closeTime === '23:59' || prevEntry.closeTime === '24:00' || prevEntry.closeTime === '00:00');
        const nextOpenIs00 = nextEntry && nextEntry.openTime === '00:00';
        const continuesFromPrev = openIs00 && prevCloseIs2359;
        const continuesToNext = closeIs2359 && nextOpenIs00;
        if (entry.is24h || (openIs00 && closeIs2359)) {
          tooltipLines.push({type: 'public', text: 'Ouvert au public 24/24'});
        } else if (continuesFromPrev && continuesToNext) {
          tooltipLines.push({type: 'public', text: 'Ouvert au public 24/24'});
        } else if (continuesFromPrev) {
          tooltipLines.push({type: 'public', text: 'Fermeture au public ' + entry.closeTime});
        } else if (continuesToNext) {
          tooltipLines.push({type: 'public', text: 'Ouverture au public ' + entry.openTime});
        } else {
          tooltipLines.push({type: 'public', text: 'Public ' + entry.openTime + ' - ' + entry.closeTime});
        }
      }
      if (isRace) {
        pill.classList.add('day-race');
        const dot = document.createElement('span');
        dot.className = 'day-indicator race';
        dotsRow.appendChild(dot);
        tooltipLines.push({type: 'race', text: 'Jour de course'});
      }
      pill.appendChild(dotsRow);
      pill.addEventListener('mouseenter', function() { _showDayNavTooltip(pill, tooltipLines); });
      pill.addEventListener('mouseleave', _hideDayNavTooltip);
    }

    pill.addEventListener('click', () => {
      const section = sectionsByDate[dateStr];
      if (!section) return;
      // Trouver le scroll container reel (varie selon le viewport :
      // .timeline-container en desktop, #timeline-main en mobile via la MQ)
      let scrollEl = section.parentElement;
      while (scrollEl && scrollEl !== document.body) {
        const oy = getComputedStyle(scrollEl).overflowY;
        if (oy === 'auto' || oy === 'scroll') break;
        scrollEl = scrollEl.parentElement;
      }
      if (scrollEl && scrollEl !== document.body) {
        const navBar = document.getElementById('day-nav-bar');
        const navOffset = (navBar && getComputedStyle(navBar).position === 'sticky') ? navBar.offsetHeight : 0;
        const offset = section.getBoundingClientRect().top - scrollEl.getBoundingClientRect().top + scrollEl.scrollTop - navOffset;
        scrollEl.scrollTo({ top: Math.max(0, offset), behavior: 'smooth' });
      } else {
        section.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
    });

    bar.appendChild(pill);
    pills[dateStr] = pill;
  });

  // Observer les sections visibles pour mettre a jour la pill active
  _setupDayNavObserver(sectionsByDate, pills);
}

function _setupDayNavObserver(sectionsByDate, pills) {
  // Nettoyer l'ancien observer
  if (_dayNavObserver) {
    _dayNavObserver.disconnect();
    _dayNavObserver = null;
  }

  const container = document.getElementById('event-list');
  if (!container) return;

  // Stocker les ratios de visibilite par date
  const visibleRatios = {};

  _dayNavObserver = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      const date = entry.target.dataset.date;
      if (date) {
        visibleRatios[date] = entry.intersectionRatio;
      }
    });

    // Trouver la date la plus visible
    let bestDate = null;
    let bestRatio = 0;
    for (const [date, ratio] of Object.entries(visibleRatios)) {
      if (ratio > bestRatio) {
        bestRatio = ratio;
        bestDate = date;
      }
    }

    // Si aucune section n'est assez visible, prendre la premiere qui intersecte
    if (!bestDate || bestRatio < 0.01) {
      for (const [date, ratio] of Object.entries(visibleRatios)) {
        if (ratio > 0) {
          bestDate = date;
          break;
        }
      }
    }

    // Mettre a jour les pills
    Object.entries(pills).forEach(([date, pill]) => {
      pill.classList.toggle('active', date === bestDate);
    });

    // Scroller la pill active dans la barre si necessaire
    if (bestDate && pills[bestDate]) {
      pills[bestDate].scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'nearest' });
    }
  }, {
    root: container,
    threshold: [0, 0.1, 0.25, 0.5, 0.75, 1.0],
  });

  // Observer toutes les sections
  Object.values(sectionsByDate).forEach(section => {
    _dayNavObserver.observe(section);
  });
}

// Fonction pour recuperer et afficher le timetable dans la timeline
async function fetchTimetable() {
    if (!window.isBlockAllowed("timeline-main")) return;
    if (!window.selectedEvent || !window.selectedYear) {
        console.error("Les variables globales 'selectedEvent' et 'selectedYear' doivent être définies.");
        return;
    }
    // S'assurer que la config de clustering est chargee avant le rendu
    await _clusterConfigReady;

    const url = '/timetable?event=' + encodeURIComponent(window.selectedEvent) + '&year=' + encodeURIComponent(window.selectedYear);

    return fetch(url)
        .then(response => response.json())
        .then(data => {
            if (typeof data.version === 'number') {
                window._timetableVersion = data.version;
            }
            const eventList = document.getElementById("event-list");
            if (eventList) eventList.innerHTML = "";  // <-- reset pour éviter les accumulations

            const sectionsByDate = {}; // Pour stocker les sections par date

            // Donnees brutes pour la modale d'ajout (suggestions lieu/departement,
            // detection de doublons, jours de l'evenement)
            window._timetableRaw = data.data || {};

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

                    // 3) Tri chronologie pure: minute croissante, puis tiebreaker alpha
                    //    sur le libelle affiche (pas le type/categorie) pour ne pas regrouper
                    //    visuellement par categorie a une meme minute.
                    const labelForNode = (n) => {
                        if (n.kind === 'cluster') {
                            const cfg = CLUSTER_CONFIG[n.data.type];
                            return (cfg?.label || n.data.type || '').toLowerCase();
                        }
                        return labelForItem(n.data);
                    };
                    combined.sort((a,b)=>{
                        if (a.minute !== b.minute) return a.minute - b.minute;
                        return labelForNode(a).localeCompare(labelForNode(b));
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
                eventList.textContent = "";
                const p = document.createElement("p");
                p.textContent = "Aucune donnee de timetable disponible.";
                eventList.appendChild(p);
            }

            // Construire la barre de navigation par jour
            _buildDayNav(Object.keys(sectionsByDate).sort(), sectionsByDate);
        })
        .catch(error => console.error("Erreur lors de la recuperation du timetable :", error));
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


// Rafraichit la timeline en conservant la position de scroll courante.
// Sans ca, le rebuild complet du DOM ramene au premier jour de la timeline.
function refreshTimetablePreservingScroll() {
    let scrollEl = document.getElementById("event-list");
    while (scrollEl && scrollEl !== document.body) {
        const oy = getComputedStyle(scrollEl).overflowY;
        if (oy === 'auto' || oy === 'scroll') break;
        scrollEl = scrollEl.parentElement;
    }
    if (scrollEl === document.body) scrollEl = null;
    const saved = scrollEl ? scrollEl.scrollTop : 0;
    return Promise.resolve(fetchTimetable()).then(() => {
        if (scrollEl) requestAnimationFrame(() => { scrollEl.scrollTop = saved; });
    });
}

// Expose fetchTimetable, fetchParametrage et openEventDrawer globalement pour main.js
window.fetchTimetable = fetchTimetable;
window.fetchParametrage = fetchParametrage;
window.openEventDrawer = openEventDrawer;

/////////////////////////////////////////////////////////////////////////////////////////////////////
// MODALE AJOUT / EDITION D'UNE VIGNETTE TIMETABLE
// Point d'entree unique : window.openTimetableModal({ mode, date, item }).
// Les vignettes issues du parametrage (param_id) n'ont que remarque et taches
// modifiables : le merge reecrit les autres champs a chaque synchronisation.
/////////////////////////////////////////////////////////////////////////////////////////////////////

(function () {
  const modal = document.getElementById('addEventModal');
  const form  = document.getElementById('addEventForm');
  if (!modal || !form) return;

  const $ = (id) => document.getElementById(id);
  const F = {
    date: $('event-date'), start: $('start-time'), end: $('end-time'), duration: $('duration'),
    category: $('category'), activity: $('activity'), place: $('place'), department: $('department'),
    remark: $('remark'), id: $('edit-id-hidden'), prep: $('prep-status-hidden'),
  };
  const UI = {
    title: $('addEventTitle'), sub: $('tt-head-sub'), icon: $('tt-head-icon'), badges: $('tt-head-badges'),
    banner: $('tt-param-banner'), days: $('tt-days'), dateWarn: $('tt-date-warning'),
    timeNote: $('tt-time-note'), durAuto: $('duration-auto'), dupWarn: $('tt-dup-warning'),
    counter: $('activity-counter'), todoCount: $('tt-todo-count'), todoList: $('event-todo-list'),
    save: $('saveEvent'), saveNew: $('saveAndNewEvent'), cancel: $('cancelAddEvent'), close: $('closeAddEvent'),
    catManager: $('category-manager'),
  };
  const TIME_NOTE_DEFAULT = 'Au moins une heure, début ou fin. TBC = heure à confirmer.';

  const state = {
    mode: 'add', item: null, locked: false, durationManual: false,
    snapshot: '', saving: false, closeTimer: null, returnFocus: null,
  };

  // ------------------------------------------------------------------
  // Heures
  // ------------------------------------------------------------------
  // "7:05", "07:05", "15h30", "7h", "7.30" -> "HH:MM" ; null si illisible
  function normTime(raw) {
    const s = String(raw || '').trim();
    let m = s.match(/^(\d{1,2})\s*[:hH.]\s*(\d{2})$/);
    let h, mi;
    if (m) { h = +m[1]; mi = +m[2]; }
    else if ((m = s.match(/^(\d{1,2})\s*[hH]$/))) { h = +m[1]; mi = 0; }
    else return null;
    if (mi > 59 || h > 24 || (h === 24 && mi !== 0)) return null;
    return String(h).padStart(2, '0') + ':' + String(mi).padStart(2, '0');
  }
  const toMin = (hhmm) => { const [h, m] = hhmm.split(':').map(Number); return h * 60 + m; };
  const fmtDur = (min) => String(Math.floor(min / 60)).padStart(2, '0') + ':' + String(min % 60).padStart(2, '0');

  const tbcBtn = (input) => form.querySelector('.tt-tbc[data-for="' + input.id + '"]');
  const isTbc  = (input) => tbcBtn(input)?.getAttribute('aria-pressed') === 'true';

  function setTbc(input, on) {
    const btn = tbcBtn(input);
    if (btn) btn.setAttribute('aria-pressed', on ? 'true' : 'false');
    input.closest('.tt-time')?.classList.toggle('is-tbc', !!on);
    if (on) input.value = '';
    input.disabled = !!on || state.locked;
  }

  // Remplit un champ heure depuis une valeur stockee. Renvoie la valeur
  // d'origine si elle n'est pas representable (ex. "Matin"), sinon null.
  function setTimeField(input, raw) {
    const s = String(raw || '').trim();
    if (s.toUpperCase() === 'TBC') { setTbc(input, true); return null; }
    setTbc(input, false);
    const n = normTime(s);
    if (n === '24:00') { input.value = '23:59'; return null; }
    input.value = n || '';
    return (s && !n) ? s : null;
  }
  const getTime = (input) => isTbc(input) ? 'TBC' : (input.value || '');

  function computedDuration() {
    const s = getTime(F.start), e = getTime(F.end);
    if (!/^\d{2}:\d{2}$/.test(s) || !/^\d{2}:\d{2}$/.test(e)) return null;
    let d = toMin(e) - toMin(s);
    const nextDay = d < 0;
    if (nextDay) d += 1440;
    return { value: fmtDur(d), nextDay };
  }

  function refreshTimeHelpers() {
    const c = computedDuration();
    if (!state.durationManual) F.duration.value = c ? c.value : '';
    UI.durAuto.hidden = !(c && !state.durationManual);
    if (!UI.timeNote.dataset.legacy) {
      UI.timeNote.textContent = (c && c.nextDay)
        ? 'La fin est avant le début : elle est comprise comme le lendemain.'
        : TIME_NOTE_DEFAULT;
      UI.timeNote.classList.toggle('tt-note-warn', !!(c && c.nextDay));
    }
  }

  // ------------------------------------------------------------------
  // Jours de l'evenement, suggestions, doublons
  // ------------------------------------------------------------------
  const ymd = (d) => d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');

  function eventRange() {
    const gh = window.parametrage?.globalHoraires || window.parametrage?.data?.globalHoraires || {};
    const a = gh.montage?.start ? new Date(gh.montage.start) : null;
    const b = gh.demontage?.end ? new Date(gh.demontage.end) : null;
    if (a && b && !isNaN(a) && !isNaN(b) && b >= a) return { start: ymd(a), end: ymd(b) };
    return null;
  }

  function eventDays() {
    const range = eventRange();
    const days = new Set();
    if (range) {
      const d = new Date(range.start + 'T12:00:00');
      const end = new Date(range.end + 'T12:00:00');
      let guard = 0;
      while (d <= end && guard++ < 62) { days.add(ymd(d)); d.setDate(d.getDate() + 1); }
    }
    Object.keys(window._timetableRaw || {}).forEach(k => /^\d{4}-\d{2}-\d{2}$/.test(k) && days.add(k));
    Object.keys(window.publicDatesMap || {}).forEach(k => days.add(k));
    return { range, list: Array.from(days).sort() };
  }

  function renderDays() {
    const { range, list } = eventDays();
    UI.days.innerHTML = '';
    // Au-dela de trois semaines les puces n'aident plus : le calendrier suffit
    if (!list.length || list.length > 21) { UI.days.hidden = true; }
    else {
      UI.days.hidden = false;
      list.forEach(ds => {
        const d = new Date(ds + 'T12:00:00');
        const b = document.createElement('button');
        b.type = 'button';
        b.className = 'tt-day tt-lockable' + (window.publicDatesMap?.[ds] ? ' is-public' : '');
        b.dataset.date = ds;
        b.title = d.toLocaleDateString('fr-FR', { weekday: 'long', day: 'numeric', month: 'long' })
          + (window.publicDatesMap?.[ds] ? ' (ouvert au public)' : '');
        const wd = document.createElement('span');
        wd.className = 'tt-day-wd';
        wd.textContent = d.toLocaleDateString('fr-FR', { weekday: 'short' }).replace('.', '');
        const dn = document.createElement('span');
        dn.className = 'tt-day-num';
        dn.textContent = d.getDate();
        b.append(wd, dn);
        b.addEventListener('click', () => {
          if (state.locked) return;
          F.date.value = ds;
          onDateChange();
        });
        UI.days.appendChild(b);
      });
    }
    return { range, list };
  }

  function onDateChange() {
    UI.days.querySelectorAll('.tt-day').forEach(b => b.classList.toggle('is-active', b.dataset.date === F.date.value));
    const range = eventRange();
    const v = F.date.value;
    const out = !!(range && v && (v < range.start || v > range.end));
    UI.dateWarn.hidden = !out;
    if (out) {
      UI.dateWarn.textContent = 'Hors de la période de l\'événement (montage '
        + new Date(range.start + 'T12:00:00').toLocaleDateString('fr-FR') + ' au démontage '
        + new Date(range.end + 'T12:00:00').toLocaleDateString('fr-FR') + ').';
    }
    checkDuplicate();
  }

  // Valeurs deja utilisees dans la timetable : [[valeur, nb vignettes], ...]
  const suggestions = { place: [], department: [] };

  function fillSuggestions() {
    const count = (field) => {
      const freq = new Map();
      Object.values(window._timetableRaw || {}).forEach(items => (items || []).forEach(it => {
        const v = String(it?.[field] || '').trim();
        if (v) freq.set(v, (freq.get(v) || 0) + 1);
      }));
      return Array.from(freq.entries())
        .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0], 'fr'));
    };
    suggestions.place = count('place');
    suggestions.department = count('department');
  }

  // ------------------------------------------------------------------
  // Liste de suggestions maison (remplace <datalist>, dont le rendu est
  // celui de l'autocompletion du navigateur). Placee en absolu dans
  // .modal-content, ouverte vers le haut s'il n'y a pas la place dessous.
  // ------------------------------------------------------------------
  const SUGGEST_MAX = 8;
  const suggestBox = document.createElement('div');
  suggestBox.className = 'tt-suggest';
  suggestBox.setAttribute('role', 'listbox');
  suggestBox.hidden = true;
  modal.querySelector('.modal-content').appendChild(suggestBox);
  const sg = { input: null, field: null, items: [], active: -1 };

  function closeSuggest() {
    suggestBox.hidden = true;
    sg.input?.setAttribute('aria-expanded', 'false');
    sg.input = null;
    sg.active = -1;
  }

  function placeSuggest() {
    if (suggestBox.hidden || !sg.input) return;
    const c = modal.querySelector('.modal-content').getBoundingClientRect();
    const r = sg.input.getBoundingClientRect();
    const m = 8, h = suggestBox.offsetHeight;
    suggestBox.style.left = (r.left - c.left) + 'px';
    suggestBox.style.width = r.width + 'px';
    let top = r.bottom - c.top + 4;
    if (top + h > c.height - m) top = Math.max(m, r.top - c.top - 4 - h);
    suggestBox.style.top = top + 'px';
  }

  function renderSuggest() {
    const input = sg.input;
    if (!input) return;
    const q = normText(input.value);
    const all = suggestions[sg.field] || [];
    // Correspondance en debut de mot d'abord, puis n'importe ou
    const scored = [];
    all.forEach(([v, n]) => {
      const t = normText(v);
      if (!q) { scored.push([v, n, 0]); return; }
      const i = t.indexOf(q);
      if (i < 0 || t === q) return;
      scored.push([v, n, (i === 0 || t[i - 1] === ' ') ? 0 : 1]);
    });
    scored.sort((a, b) => a[2] - b[2] || b[1] - a[1]);
    sg.items = scored.slice(0, SUGGEST_MAX);
    sg.active = -1;

    suggestBox.textContent = '';
    if (!sg.items.length) { closeSuggest(); return; }
    const head = document.createElement('div');
    head.className = 'tt-suggest-head';
    head.textContent = q ? 'Déjà utilisés' : 'Les plus utilisés';
    suggestBox.appendChild(head);

    sg.items.forEach(([v, n], idx) => {
      const opt = document.createElement('div');
      opt.className = 'tt-suggest-item';
      opt.setAttribute('role', 'option');
      opt.dataset.idx = idx;
      const label = document.createElement('span');
      label.className = 'tt-suggest-label';
      // Surligne la partie saisie (sur le texte d'origine, meme longueur hors accents composes)
      const t = normText(v), i = q ? t.indexOf(q) : -1;
      if (i >= 0 && v.length === t.length) {
        label.append(v.slice(0, i));
        const mk = document.createElement('mark');
        mk.textContent = v.slice(i, i + q.length);
        label.append(mk, v.slice(i + q.length));
      } else {
        label.textContent = v;
      }
      const cnt = document.createElement('span');
      cnt.className = 'tt-suggest-count';
      cnt.textContent = n;
      cnt.title = n + ' vignette(s)';
      opt.append(label, cnt);
      // mousedown : avant le blur de l'input
      opt.addEventListener('mousedown', (e) => { e.preventDefault(); pickSuggest(idx); });
      opt.addEventListener('mousemove', () => setActive(idx));
      suggestBox.appendChild(opt);
    });
    suggestBox.hidden = false;
    input.setAttribute('aria-expanded', 'true');
    placeSuggest();
  }

  function setActive(idx) {
    sg.active = idx;
    suggestBox.querySelectorAll('.tt-suggest-item').forEach((el, i) => {
      el.classList.toggle('is-active', i === idx);
      if (i === idx) el.scrollIntoView({ block: 'nearest' });
    });
  }

  function pickSuggest(idx) {
    const it = sg.items[idx];
    if (!it || !sg.input) return;
    const input = sg.input;
    input.value = it[0];
    input.classList.remove('input-error');
    closeSuggest();
    input.focus();
  }

  function attachSuggest(input, field) {
    input.setAttribute('role', 'combobox');
    input.setAttribute('aria-autocomplete', 'list');
    input.setAttribute('aria-expanded', 'false');
    const open = () => { if (input.disabled) return; sg.input = input; sg.field = field; renderSuggest(); };
    input.addEventListener('focus', open);
    input.addEventListener('click', () => { if (suggestBox.hidden) open(); });
    input.addEventListener('input', open);
    input.addEventListener('blur', () => setTimeout(() => { if (sg.input === input) closeSuggest(); }, 120));
    input.addEventListener('keydown', (e) => {
      if (suggestBox.hidden || sg.input !== input) {
        if (e.key === 'ArrowDown') { e.preventDefault(); open(); }
        return;
      }
      if (e.key === 'ArrowDown') { e.preventDefault(); setActive(Math.min(sg.active + 1, sg.items.length - 1)); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); setActive(Math.max(sg.active - 1, 0)); }
      else if (e.key === 'Enter' && sg.active >= 0 && !e.ctrlKey && !e.metaKey) { e.preventDefault(); pickSuggest(sg.active); }
      else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); closeSuggest(); }
      else if (e.key === 'Tab') closeSuggest();
    });
  }
  attachSuggest(F.place, 'place');
  attachSuggest(F.department, 'department');
  modal.querySelector('.modal-body')?.addEventListener('scroll', placeSuggest, { passive: true });
  window.addEventListener('resize', placeSuggest);

  const normText = (s) => String(s || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').trim().toLowerCase().replace(/\s+/g, ' ');

  function checkDuplicate() {
    const act = normText(F.activity.value);
    const items = (window._timetableRaw || {})[F.date.value] || [];
    const selfId = F.id.value;
    const hit = act && items.find(it => it && String(it._id || '') !== selfId && normText(it.activity) === act);
    UI.dupWarn.hidden = !hit;
    if (hit) {
      const h = [hit.start, hit.end].filter(x => x && x !== 'TBC').map(formatHHMM).join(' - ') || 'heure non précisée';
      UI.dupWarn.textContent = 'Une vignette porte déjà ce nom ce jour-là (' + h + '). Vérifier qu\'il ne s\'agit pas d\'un doublon.';
    }
  }

  function updateCounter() {
    const n = F.activity.value.length;
    UI.counter.textContent = n > 150 ? (n + ' / 200') : '';
  }

  // ------------------------------------------------------------------
  // Taches
  // ------------------------------------------------------------------
  function updateTodoCount() {
    const rows = Array.from(UI.todoList.querySelectorAll('.event-todo-row'))
      .filter(r => (r.querySelector('input[type="text"]')?.value || '').trim());
    const done = rows.filter(r => r.dataset.done === '1').length;
    // A la creation, pas d'avancement a montrer : juste le nombre de taches
    const add = modal.classList.contains('is-add');
    UI.todoCount.textContent = !rows.length ? '' : (add ? String(rows.length) : (done + '/' + rows.length));
    UI.todoCount.classList.toggle('is-complete', !add && rows.length > 0 && done === rows.length);
  }
  UI.todoList.addEventListener('input', updateTodoCount);
  UI.todoList.addEventListener('change', updateTodoCount);
  UI.todoList.addEventListener('click', () => setTimeout(updateTodoCount, 0));

  // ------------------------------------------------------------------
  // Etat du formulaire
  // ------------------------------------------------------------------
  function collect() {
    return {
      date: F.date.value,
      start: getTime(F.start),
      end: getTime(F.end),
      duration: F.duration.value.trim(),
      category: F.category.value,
      activity: F.activity.value.trim(),
      place: F.place.value.trim(),
      department: F.department.value.trim(),
      remark: F.remark.value,
      todo: (typeof window.collectEventTodos === 'function' ? window.collectEventTodos() : ''),
      preparation_checked: F.prep.value || '',
    };
  }
  const takeSnapshot = () => { state.snapshot = JSON.stringify(collect()); };
  const isDirty = () => JSON.stringify(collect()) !== state.snapshot;

  function clearErrors() {
    form.querySelectorAll('.form-error').forEach(n => n.remove());
    form.querySelectorAll('.input-error').forEach(n => n.classList.remove('input-error'));
  }

  function markError(el, msg, focusList) {
    el.classList.add('input-error');
    const host = el.closest('.form-group');
    if (host && !host.querySelector('.form-error')) {
      const err = document.createElement('div');
      err.className = 'form-error';
      err.textContent = msg;
      host.appendChild(err);
    }
    focusList.push(el);
  }

  function validate(v) {
    clearErrors();
    if (state.locked) return true;
    const bad = [];
    if (!/^\d{4}-\d{2}-\d{2}$/.test(v.date)) markError(F.date, 'La date est obligatoire.', bad);
    if (!v.start && !v.end) markError(F.start, 'Saisir au moins une heure (début ou fin), ou TBC.', bad);
    if (!v.activity) markError(F.activity, 'L\'activité est obligatoire.', bad);
    if (!v.category) markError(F.category, 'La catégorie est obligatoire.', bad);
    if (v.duration && !/^\d{1,3}:\d{2}$/.test(v.duration)) markError(F.duration, 'Format hh:mm.', bad);
    if (bad.length) { bad[0].focus(); return false; }
    return true;
  }

  function setLocked(locked) {
    state.locked = locked;
    UI.banner.hidden = !locked;
    modal.classList.toggle('is-locked', locked);
    form.querySelectorAll('.tt-lockable').forEach(el => { el.disabled = locked; });
    // Les champs heure en TBC restent desactives meme deverrouilles
    [F.start, F.end].forEach(inp => { inp.disabled = locked || isTbc(inp); });
    if (locked) closeCatManager();
  }

  function setSaving(on) {
    state.saving = on;
    modal.classList.toggle('is-saving', on);
    [UI.save, UI.saveNew, UI.cancel].forEach(b => { if (b) b.disabled = on; });
  }

  function badge(text, cls, icon) {
    const b = document.createElement('span');
    b.className = 'tt-badge ' + (cls || '');
    if (icon) {
      const i = document.createElement('span');
      i.className = 'material-symbols-outlined';
      i.textContent = icon;
      b.appendChild(i);
    }
    b.appendChild(document.createTextNode(text));
    return b;
  }

  function renderHeader() {
    const edit = state.mode === 'edit';
    UI.title.textContent = edit ? 'Modifier l\'événement' : 'Nouvel événement';
    UI.icon.textContent = edit ? 'edit_calendar' : 'event_available';
    UI.sub.textContent = 'Timetable ' + (window.selectedEvent || '') + ' ' + (window.selectedYear || '');
    UI.badges.innerHTML = '';
    if (!edit) return;
    const it = state.item || {};
    if (state.locked) UI.badges.appendChild(badge('Paramétrage', 'is-param', 'lock'));
    else if (it.origin === 'duplicate') UI.badges.appendChild(badge('Copie', 'is-copy', 'content_copy'));
    else UI.badges.appendChild(badge('Saisie manuelle', 'is-manual', 'edit'));
    const prep = String(it.preparation_checked || '').toLowerCase();
    if (prep === 'true') UI.badges.appendChild(badge('Prête', 'is-ready', 'task_alt'));
    else if (prep === 'progress') UI.badges.appendChild(badge('En préparation', 'is-progress', 'pending'));
  }

  // ------------------------------------------------------------------
  // Ouverture / fermeture
  // ------------------------------------------------------------------
  function show() {
    clearTimeout(state.closeTimer);
    state.returnFocus = document.activeElement;
    modal.style.display = 'flex';
    const body = modal.querySelector('.modal-body');
    if (body) body.scrollTop = 0;
    requestAnimationFrame(() => {
      modal.classList.add('show');
      setTimeout(() => (state.locked ? F.remark : F.activity).focus(), 60);
    });
  }

  function hide() {
    closeSuggest();
    closeCatManager();
    modal.classList.remove('show');
    state.closeTimer = setTimeout(() => { modal.style.display = 'none'; }, 250);
    try { state.returnFocus?.focus?.(); } catch (e) {}
  }

  async function dismiss() {
    if (state.saving || state.confirming) return;
    if (isDirty()) {
      state.confirming = true;
      let ok = true;
      try {
        ok = (typeof showConfirmToast === 'function')
          ? await showConfirmToast('Fermer sans enregistrer les modifications ?', { type: 'warning', okLabel: 'Fermer', cancelLabel: 'Continuer' })
          : true;
      } finally {
        state.confirming = false;
      }
      if (!ok) return;
    }
    hide();
  }

  function defaultDate(list) {
    const today = ymd(new Date());
    if (list.includes(today)) return today;
    const range = eventRange();
    if (range && today >= range.start && today <= range.end) return today;
    return list.find(d => d >= today) || list[0] || today;
  }

  window.openTimetableModal = async function (opts) {
    opts = opts || {};
    if (!window.selectedEvent || !window.selectedYear) {
      showToast('error', 'Sélectionnez d\'abord un événement et une année.');
      return;
    }
    const edit = opts.mode === 'edit' && opts.item;
    const item = edit ? opts.item : null;
    state.mode = edit ? 'edit' : 'add';
    state.item = item;
    state.durationManual = false;
    delete UI.timeNote.dataset.legacy;

    form.reset();
    clearErrors();
    closeCatManager();
    closeSuggest();
    // Cases "fait" des taches : seulement en modification (a la creation,
    // aucune tache ne peut deja etre faite)
    modal.classList.toggle('is-add', !edit);
    F.id.value = edit ? String(item._id || '') : '';
    F.prep.value = edit ? String(item.preparation_checked ?? '').toLowerCase() : '';

    await loadCategories();
    refreshEventCategorySelect(edit ? (item.category || '') : '');
    fillSuggestions();
    const { list } = renderDays();

    // Verrouillage avant remplissage : setTimeField en tient compte
    setLocked(!!(edit && item.param_id && (item.origin === 'parametrage' || item.origin === 'manual-edit')));

    if (edit) {
      F.date.value = String(opts.date || item.date || '').slice(0, 10);
      const legacy = [setTimeField(F.start, item.start), setTimeField(F.end, item.end)];
      F.activity.value = item.activity || '';
      F.place.value = item.place || '';
      F.department.value = item.department || '';
      F.remark.value = item.remark || '';
      if (window.populateEventTodoEditor) window.populateEventTodoEditor(item.todo || '');
      const c = computedDuration();
      const dur = String(item.duration || '').trim();
      state.durationManual = !!dur && !(c && c.value === dur);
      F.duration.value = dur;
      if (legacy[0] || legacy[1]) {
        UI.timeNote.dataset.legacy = '1';
        UI.timeNote.classList.add('tt-note-warn');
        UI.timeNote.textContent = 'Valeur d\'origine non reconnue : "'
          + (legacy[0] ? 'début ' + legacy[0] : '') + (legacy[0] && legacy[1] ? ', ' : '')
          + (legacy[1] ? 'fin ' + legacy[1] : '') + '". Ressaisir l\'heure au format hh:mm ou TBC.';
      }
    } else {
      F.date.value = opts.date || defaultDate(list);
      setTimeField(F.start, '');
      setTimeField(F.end, '');
      if (window.populateEventTodoEditor) window.populateEventTodoEditor('');
    }
    setLocked(state.locked);   // re-applique sur les champs heure TBC
    UI.saveNew.hidden = !!edit;

    refreshTimeHelpers();
    onDateChange();
    updateCounter();
    updateTodoCount();
    renderHeader();
    takeSnapshot();
    show();
  };

  // ------------------------------------------------------------------
  // Enregistrement
  // ------------------------------------------------------------------
  async function submit(keepOpen) {
    if (state.saving) return;
    const v = collect();
    if (!validate(v)) return;

    const edit = state.mode === 'edit';
    const payload = Object.assign({ event: window.selectedEvent, year: window.selectedYear }, v);
    if (edit) payload._id = F.id.value;
    if (edit && !payload._id) { showToast('error', 'Identifiant de la vignette manquant, impossible de modifier.'); return; }

    setSaving(true);
    try {
      const res = await fetch(edit ? '/update_timetable_event' : '/add_timetable_event', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': _csrfToken() },
        body: JSON.stringify(payload),
      });
      let data = {};
      try { data = await res.json(); } catch (e) {}
      if (!res.ok || !data.success) {
        showToast('error', data.message || ('Enregistrement impossible (HTTP ' + res.status + ').'));
        return;
      }
      showToast('success', edit ? 'Événement modifié.' : 'Événement ajouté.');
      refreshTimetablePreservingScroll();

      if (keepOpen && !edit) {
        // Enchainer une saisie : on garde date, categorie, lieu, departement
        F.activity.value = '';
        F.remark.value = '';
        setTimeField(F.start, '');
        setTimeField(F.end, '');
        state.durationManual = false;
        if (window.populateEventTodoEditor) window.populateEventTodoEditor('');
        // La vignette tout juste creee compte pour la detection de doublon
        const raw = window._timetableRaw || (window._timetableRaw = {});
        (raw[v.date] = raw[v.date] || []).push(Object.assign({ _id: data._id || '' }, v));
        clearErrors();
        refreshTimeHelpers();
        checkDuplicate();
        updateCounter();
        updateTodoCount();
        takeSnapshot();
        F.activity.focus();
      } else {
        takeSnapshot();
        hide();
      }
    } catch (e) {
      showToast('error', 'Erreur réseau : événement non enregistré.');
    } finally {
      setSaving(false);
      setLocked(state.locked);
    }
  }

  // ------------------------------------------------------------------
  // Evenements
  // ------------------------------------------------------------------
  document.getElementById('add-event-button')?.addEventListener('click', () => window.openTimetableModal({ mode: 'add' }));

  form.addEventListener('submit', (e) => { e.preventDefault(); submit(false); });
  UI.saveNew.addEventListener('click', () => submit(true));
  UI.cancel.addEventListener('click', dismiss);
  UI.close.addEventListener('click', dismiss);
  modal.addEventListener('mousedown', (e) => { if (e.target === modal) dismiss(); });

  document.addEventListener('keydown', (e) => {
    if (!modal.classList.contains('show')) return;
    if (e.key === 'Escape') {
      // Echap ferme d'abord le gestionnaire de categories s'il est ouvert
      if (!UI.catManager.hasAttribute('hidden')) { e.preventDefault(); closeCatManager(); catBtn?.focus(); return; }
      e.preventDefault();
      dismiss();
    } else if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      submit(false);
    }
  });

  form.querySelectorAll('.tt-tbc').forEach(btn => btn.addEventListener('click', () => {
    const input = document.getElementById(btn.dataset.for);
    const on = !isTbc(input);
    setTbc(input, on);
    if (!on) input.focus();
    input.classList.remove('input-error');
    delete UI.timeNote.dataset.legacy;
    refreshTimeHelpers();
  }));
  [F.start, F.end].forEach(inp => inp.addEventListener('input', () => {
    inp.classList.remove('input-error');
    delete UI.timeNote.dataset.legacy;   // l'heure a ete ressaisie
    refreshTimeHelpers();
  }));
  F.duration.addEventListener('input', () => {
    state.durationManual = F.duration.value.trim() !== '';
    refreshTimeHelpers();
  });
  F.date.addEventListener('change', onDateChange);
  F.activity.addEventListener('input', () => { updateCounter(); checkDuplicate(); });
  form.addEventListener('input', (e) => e.target.classList?.remove('input-error'));

  // --- Gestionnaire de categories : popover flottant ---
  // Positionne en absolu par rapport a .modal-content (qui porte un transform,
  // donc sert de bloc conteneur) : il ne pousse pas le formulaire en hauteur.
  const catBtn = document.getElementById('category-manage-btn');
  const card = modal.querySelector('.modal-content');

  function placeCatManager() {
    if (UI.catManager.hasAttribute('hidden') || !catBtn) return;
    const c = card.getBoundingClientRect();
    const b = catBtn.getBoundingClientRect();
    const pop = UI.catManager;
    const w = pop.offsetWidth, h = pop.offsetHeight, m = 8;
    let left = b.right - c.left - w;
    left = Math.max(m, Math.min(left, c.width - w - m));
    let top = b.bottom - c.top + 6;
    if (top + h > c.height - m) {
      const above = b.top - c.top - 6 - h;   // pas la place dessous : au-dessus
      top = above >= m ? above : Math.max(m, c.height - h - m);
    }
    pop.style.left = left + 'px';
    pop.style.top = top + 'px';
  }

  function openCatManager() {
    UI.catManager.removeAttribute('hidden');
    catBtn?.setAttribute('aria-expanded', 'true');
    placeCatManager();
    loadCategoryManager().then(placeCatManager);
    document.getElementById('cat-mgr-input')?.focus();
  }

  function closeCatManager() {
    if (UI.catManager.hasAttribute('hidden')) return;
    UI.catManager.setAttribute('hidden', '');
    catBtn?.setAttribute('aria-expanded', 'false');
  }

  catBtn?.addEventListener('click', (e) => {
    e.preventDefault();      // ne pas focaliser le select (bouton dans un <label>)
    e.stopPropagation();
    if (UI.catManager.hasAttribute('hidden')) openCatManager(); else closeCatManager();
  });
  document.getElementById('cat-mgr-close')?.addEventListener('click', () => { closeCatManager(); catBtn?.focus(); });
  // Clic hors du popover : fermeture
  document.addEventListener('mousedown', (e) => {
    if (UI.catManager.hasAttribute('hidden')) return;
    if (UI.catManager.contains(e.target) || catBtn?.contains(e.target)) return;
    closeCatManager();
  });
  modal.querySelector('.modal-body')?.addEventListener('scroll', placeCatManager, { passive: true });
  window.addEventListener('resize', placeCatManager);
  document.getElementById('cat-mgr-add-btn')?.addEventListener('click', addCategoryFromManager);
  document.getElementById('cat-mgr-input')?.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); addCategoryFromManager(); }
  });

  // --- Editeur de taches : bouton d'ajout d'une tache ---
  document.getElementById('event-todo-add')?.addEventListener('click', () => {
    const row = _addEventTodoRow('');
    row?.querySelector('input[type="text"]')?.focus();
    updateTodoCount();
  });
})();

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
  const safe = structuredClone(item || {});
  if (!safe._id && safe.id) safe._id = String(safe.id);
  if (safe._id) safe._id = String(safe._id);

  _drawerCurrent = { date, item: safe };

  // 🆕 garde-fou global: l'ID est accessible même si l'objet est re-cloné
  if (drawerEl) drawerEl.dataset.eventId = safe._id || safe.id || '';

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
      <div class="value">${(it.start && it.start!=='TBC')?formatHHMM(it.start):'—'} ${(it.end && it.end!=='TBC')?(' - '+formatHHMM(it.end)):''}</div>
    </div>
    <div class="field">
      <div class="label">Catégorie</div>
      <div class="value">${it.category || '—'}</div>
    </div>
    <div class="field">
      <div class="label">Lieu</div>
      <div class="value">${it.place || '—'}</div>
    </div>
    ${(() => {
      const info = getAccessInfo(it);
      if (!info) return '';
      return `<div class="field">
      <div class="label">Accès</div>
      <div class="value"><span class="access-chip ${info.cls}"><span class="material-symbols-outlined">${info.icon}</span>${info.label}</span></div>
    </div>`;
    })()}
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

    <!-- === Contrôle direct du statut === -->
    <div class="field">
      <div class="label">Changer le statut</div>
      <div class="value">
        <div class="prep-status-group" role="group" aria-label="Statut de préparation">
          <button type="button" class="psg-btn" data-status="none"     title="Marquer 'Non'">Non</button>
          <button type="button" class="psg-btn" data-status="progress" title="Marquer 'En cours'">En cours</button>
          <button type="button" class="psg-btn" data-status="true"     title="">Prête</button>
        </div>
        <div class="psg-hint">Astuce : “Prête” est verrouillé si des tâches TODO ne sont pas cochées.</div>
      </div>
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

      // [AJOUT] Les TODO ont changé → mettre à jour les boutons (désactiver/activer "Prête")
      updatePrepControls(drawerBodyEl, _drawerCurrent.item);
    });
  });

  const addLine   = drawerBodyEl.querySelector('#todo-add-line');
  const addFirst  = drawerBodyEl.querySelector('#todo-add-first');
  const clearDone = drawerBodyEl.querySelector('#todo-clear-done');
  const saveTodo  = drawerBodyEl.querySelector('#todo-save');

  addLine?.addEventListener('click', () => {
    const list = splitTodo(_drawerCurrent.item.todo || "");
    showPromptToast('Nouvelle tache :', { okLabel: 'Ajouter' }).then((txt) => {
      if (txt && txt.trim()) {
        list.push({ text: txt.trim(), done: false });
        _drawerCurrent.item.todo = serializeTodo(list);
        renderDrawerView(); // re-render
      }
    });
  });

  addFirst?.addEventListener('click', () => {
    const list = splitTodo(_drawerCurrent.item.todo || "");
    showPromptToast('Nouvelle tache :', { okLabel: 'Ajouter' }).then((txt) => {
      if (txt && txt.trim()) {
        list.push({ text: txt.trim(), done: false });
        _drawerCurrent.item.todo = serializeTodo(list);
        renderDrawerView();
      }
    });
  });

  clearDone?.addEventListener('click', () => {
    const list = splitTodo(_drawerCurrent.item.todo || "").filter(l => !l.done);
    _drawerCurrent.item.todo = serializeTodo(list);
    renderDrawerView();
  });

  // bouton "Enregistrer TODO" manuel (au cas où)
  saveTodo?.addEventListener('click', () => {
    saveUpdate(_drawerCurrent.date, _drawerCurrent.item);

    // Les TODO ont changé → mettre à jour les contrôles de statut (disable "Prête" si besoin)
    updatePrepControls(drawerBodyEl, _drawerCurrent.item);
  });

  // (1) Clic sur les boutons "Non / En cours / Prête"
  drawerBodyEl.querySelectorAll('.prep-status-group .psg-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const target = btn.getAttribute('data-status'); // 'none' | 'progress' | 'true'
      setPrepStatusFromDrawer(_drawerCurrent.date, _drawerCurrent.item, target);
    });
  });

  // (2) État visuel initial (active/disabled + tooltip)
  updatePrepControls(drawerBodyEl, _drawerCurrent.item);

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
// Sauvegarde depuis le drawer : taches et statut de preparation (scope
// "operator" : le serveur n'ecrit que remark/todo/preparation_checked, sans
// revalider les horaires d'une vignette ancienne). Les autres champs sont
// renvoyes tels quels, sans substitution, pour rester sans effet.
function saveUpdate(dateStr, item, closeAfter = false) {
  if (!requireIdOrWarn(item)) return;
  const payload = {
    event:      window.selectedEvent,
    year:       window.selectedYear,
    date:       dateStr,
    _id:        item._id,
    scope:      'operator',
    start:      item.start ?? '',
    end:        item.end ?? '',
    duration:   item.duration ?? '',
    category:   item.category ?? '',
    activity:   item.activity ?? '',
    place:      item.place ?? '',
    department: item.department ?? '',
    remark:     item.remark || '',
    todo:       item.todo || '',
    preparation_checked: (item.preparation_checked ?? '').toString().toLowerCase()
  };
  fetch('/update_timetable_event', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': _csrfToken() },
    body: JSON.stringify(payload)
  })
  .then(r => r.json().catch(() => ({})))
  .then(res => {
    if (res.success) {
      showDynamicFlashMessage("Mise à jour réussie", "success");
      refreshTimetablePreservingScroll(); // rafraîchir la liste sans perdre la position
      closeAfter && closeEventDrawer();
      // recharger la vue lecture avec l'objet mis à jour
      !_drawerCurrent || renderDrawerView();
    } else {
      showDynamicFlashMessage(res.message || "Erreur lors de l'enregistrement", "error");
    }
  })
  .catch(() => showDynamicFlashMessage("Erreur réseau", "error"));
}

async function deleteCurrent() {
  const it = _drawerCurrent.item;
  if (!requireIdOrWarn(it)) return;
  const ok = await showConfirmToast('Supprimer définitivement "' + (it.activity || 'cet événement') + '" ?',
    { type: "error", okLabel: "Supprimer", cancelLabel: "Annuler" });
  if (!ok) return;

  try {
    const res = await fetch('/delete_timetable_event', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': _csrfToken() },
      body: JSON.stringify({
        event: window.selectedEvent,
        year:  window.selectedYear,
        date:  _drawerCurrent.date,
        _id:   it._id,
      })
    });
    const data = await res.json().catch(() => ({}));
    if (data.success) {
      showDynamicFlashMessage("Événement supprimé", "success");
      closeEventDrawer();
      refreshTimetablePreservingScroll();
    } else {
      showDynamicFlashMessage(data.message || "Suppression impossible", "error");
    }
  } catch (e) {
    showDynamicFlashMessage("Erreur réseau", "error");
  }
}

// Duplication : demande la date cible (par defaut le meme jour). La copie est
// une vignette manuelle, detachee du parametrage, taches decochees.
async function duplicateCurrent() {
  const it = _drawerCurrent.item;
  if (!requireIdOrWarn(it)) return;
  const target = await showPromptToast('Dupliquer vers quelle date ?', {
    defaultValue: _drawerCurrent.date, inputType: 'date', okLabel: 'Dupliquer'
  });
  if (target === null || target === undefined) return;
  if (!/^\d{4}-\d{2}-\d{2}$/.test(target)) {
    showDynamicFlashMessage("Date invalide", "error");
    return;
  }

  try {
    const res = await fetch('/duplicate_timetable_event', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': _csrfToken() },
      body: JSON.stringify({
        event: window.selectedEvent,
        year:  window.selectedYear,
        date:  _drawerCurrent.date,
        _id:   it._id,
        target_date: target,
      })
    });
    const data = await res.json().catch(() => ({}));
    if (data.success) {
      showDynamicFlashMessage(target === _drawerCurrent.date
        ? "Événement dupliqué"
        : "Événement dupliqué au " + new Date(target + 'T12:00:00').toLocaleDateString('fr-FR'), "success");
      refreshTimetablePreservingScroll();
    } else {
      showDynamicFlashMessage(data.message || "Erreur lors de la duplication", "error");
    }
  } catch (e) {
    showDynamicFlashMessage("Erreur réseau", "error");
  }
}

// ---- Chargement (et cache) des catégories ----
// Cache par couple evenement/annee : changer d'evenement doit recharger la liste.
async function loadCategories() {
  const key = (window.selectedEvent || '') + '|' + (window.selectedYear || '');
  try {
    if (Array.isArray(window.categories) && window.categories.length && window._categoriesKey === key) {
      return window.categories; // cache
    }
    if (!window.selectedEvent || !window.selectedYear) {
      window.categories = [];
      return window.categories;
    }
    const url = '/get_timetable_categories?event=' +
      encodeURIComponent(window.selectedEvent) +
      '&year=' + encodeURIComponent(window.selectedYear);

    const res = await fetch(url);
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const data = await res.json();
    const list = Array.isArray(data?.categories) ? data.categories : [];
    // Tri alphabetique insensible a la casse (le backend trie deja, filet de securite)
    list.sort((a, b) => String(a).localeCompare(String(b), 'fr', { sensitivity: 'base' }));
    window.categories = list;
    window._categoriesKey = key;
    return window.categories;
  } catch (e) {
    console.error('[loadCategories] échec:', e);
    window.categories = window.categories || [];
    return window.categories;
  }
}

// =====================================================================
// Modale ajout/edition : editeur de taches de la vignette + gestionnaire
// de categories (ajout / suppression des orphelines).
// =====================================================================

function _csrfToken() {
  return document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';
}

// ---- Editeur de taches (TODO) propre a la vignette ----
function _addEventTodoRow(text, done) {
  const list = document.getElementById('event-todo-list');
  if (!list) return null;
  const row = document.createElement('div');
  row.className = 'event-todo-row' + (done ? ' is-done' : '');
  row.dataset.done = done ? '1' : '';

  const check = document.createElement('input');
  check.type = 'checkbox';
  check.className = 'event-todo-check';
  check.checked = !!done;
  check.title = 'Tâche faite';
  check.addEventListener('change', () => {
    row.dataset.done = check.checked ? '1' : '';
    row.classList.toggle('is-done', check.checked);
  });

  const input = document.createElement('input');
  input.type = 'text';
  input.className = 'form-input';
  input.placeholder = 'Nouvelle tâche...';
  input.maxLength = 300;
  input.value = text || '';
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.ctrlKey && !e.metaKey) {
      e.preventDefault();
      if (input.value.trim()) {
        const r = _addEventTodoRow('');
        row.after(r);
        r.querySelector('input[type="text"]').focus();
      }
    } else if (e.key === 'Backspace' && !input.value) {
      // Ligne vide + retour arriere : on la retire et on remonte
      const prev = row.previousElementSibling;
      if (prev) {
        e.preventDefault();
        row.remove();
        prev.querySelector('input[type="text"]')?.focus();
        list.dispatchEvent(new Event('input'));
      }
    }
  });

  const rm = document.createElement('button');
  rm.type = 'button';
  rm.className = 'event-todo-remove';
  rm.title = 'Supprimer la tâche';
  rm.setAttribute('aria-label', 'Supprimer la tâche');
  const icon = document.createElement('span');
  icon.className = 'material-symbols-outlined';
  icon.textContent = 'close';
  rm.appendChild(icon);
  rm.addEventListener('click', () => row.remove());

  row.append(check, input, rm);
  list.appendChild(row);
  return row;
}

// Remplit l'editeur depuis une chaine markdown ('- [ ] ...'), preservant l'etat coche.
window.populateEventTodoEditor = function (todoStr) {
  const list = document.getElementById('event-todo-list');
  if (!list) return;
  list.innerHTML = '';
  splitTodo(todoStr || '').forEach(it => _addEventTodoRow(it.text, it.done));
};

// Reconstruit la chaine markdown a partir des lignes (etat coche preserve par ligne).
window.collectEventTodos = function () {
  const list = document.getElementById('event-todo-list');
  if (!list) return '';
  const items = Array.from(list.querySelectorAll('.event-todo-row'))
    .map(row => ({
      text: (row.querySelector('input[type="text"]')?.value || '').trim(),
      done: row.dataset.done === '1',
    }))
    .filter(it => it.text);
  return serializeTodo(items);
};

// ---- Gestionnaire de categories ----
// Reconstruit le <select> depuis window.categories (deja trie), conserve la selection.
// Une option vide "Choisir..." force un choix explicite a la creation ; une
// categorie absente de la liste (vignette ancienne) est conservee telle quelle.
function refreshEventCategorySelect(selected) {
  const sel = document.getElementById('category');
  if (!sel) return;
  const keep = (selected != null) ? selected : sel.value;
  sel.innerHTML = '';
  const ph = document.createElement('option');
  ph.value = '';
  ph.textContent = 'Choisir une catégorie...';
  ph.disabled = true;
  sel.appendChild(ph);
  const cats = (window.categories || []).slice();
  if (keep && !cats.includes(keep)) cats.push(keep);
  cats.forEach(cat => {
    const o = document.createElement('option');
    o.value = cat;
    o.textContent = cat;
    sel.appendChild(o);
  });
  sel.value = keep || '';
  if (!keep) ph.selected = true;
}

async function loadCategoryManager() {
  const listEl = document.getElementById('cat-mgr-list');
  if (!listEl) return;
  listEl.innerHTML = '<div class="cat-mgr-empty">Chargement…</div>';
  try {
    const url = '/api/timetable-categories?event=' +
      encodeURIComponent(window.selectedEvent) +
      '&year=' + encodeURIComponent(window.selectedYear);
    const res = await fetch(url);
    const data = await res.json();
    const cats = data.categories || [];
    listEl.innerHTML = '';
    if (!cats.length) { listEl.innerHTML = '<div class="cat-mgr-empty">Aucune catégorie.</div>'; return; }
    cats.forEach(c => {
      const row = document.createElement('div');
      row.className = 'cat-mgr-row';

      const name = document.createElement('span');
      name.className = 'cat-mgr-name';
      name.textContent = c.name;

      const count = document.createElement('span');
      count.className = 'cat-mgr-count' + (c.orphan ? ' is-orphan' : '');
      count.textContent = c.count;
      count.title = c.count + ' vignette(s)';

      row.appendChild(name);
      row.appendChild(count);

      if (c.orphan) {
        const del = document.createElement('button');
        del.type = 'button';
        del.className = 'cat-mgr-del';
        del.title = 'Supprimer cette catégorie orpheline';
        del.innerHTML = '<span class="material-symbols-outlined">delete</span>';
        del.addEventListener('click', () => deleteCategoryOrphan(c.name));
        row.appendChild(del);
      }
      listEl.appendChild(row);
    });
  } catch (e) {
    listEl.innerHTML = '<div class="cat-mgr-empty">Erreur de chargement.</div>';
  }
}

async function addCategoryFromManager() {
  const input = document.getElementById('cat-mgr-input');
  const cat = (input?.value || '').trim();
  if (!cat) return;
  try {
    const res = await fetch('/api/timetable-categories', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': _csrfToken() },
      body: JSON.stringify({ event: window.selectedEvent, year: window.selectedYear, category: cat }),
    });
    const data = await res.json();
    if (!data.ok) { showDynamicFlashMessage("Ajout impossible", "error"); return; }
    input.value = '';
    window.categories = null;            // invalider le cache
    await loadCategories();
    refreshEventCategorySelect(cat);     // selectionner la nouvelle categorie
    await loadCategoryManager();
    showDynamicFlashMessage("Catégorie ajoutée", "success");
  } catch (e) {
    showDynamicFlashMessage("Erreur réseau", "error");
  }
}

async function deleteCategoryOrphan(cat) {
  const ok = (typeof showConfirmToast === 'function')
    ? await showConfirmToast('Supprimer la catégorie "' + cat + '" ?', { type: "error", okLabel: "Supprimer" })
    : window.confirm('Supprimer la catégorie "' + cat + '" ?');
  if (!ok) return;
  try {
    const res = await fetch('/api/timetable-categories', {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': _csrfToken() },
      body: JSON.stringify({ event: window.selectedEvent, year: window.selectedYear, category: cat }),
    });
    const data = await res.json();
    if (!data.ok) {
      showDynamicFlashMessage(data.error === 'not_orphan'
        ? 'Catégorie utilisée par ' + (data.count || 0) + ' vignette(s), suppression refusée.'
        : 'Suppression impossible', "error");
      return;
    }
    window.categories = null;
    await loadCategories();
    refreshEventCategorySelect();
    await loadCategoryManager();
    showDynamicFlashMessage("Catégorie supprimée", "success");
  } catch (e) {
    showDynamicFlashMessage("Erreur réseau", "error");
  }
}

async function openEditModalFromDrawer(dateStr, item) {
  // Fermer le drawer AVANT d'ouvrir la modale (evite le warning ARIA)
  closeEventDrawer();
  document.activeElement && document.activeElement.blur?.();
  await window.openTimetableModal({ mode: 'edit', date: dateStr, item });
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
          // Date en FUSEAU LOCAL (important pour éviter l'effet UTC)
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
      if (this._mode !== 'sim') { console.warn("[Clock] play: passe d'abord en mode simule avec setSim"); return; }
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

    // Regroupement par minute identique → moyenne de y (stabilise l'interpolation)
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
      // s'assure que la ligne existe bien DANS #timeline-main
      const main = document.getElementById('timeline-main');
      let el = document.getElementById('now-line');

      if (!main) { console.warn('[NowLine] timeline-main introuvable'); return; }

      if (!el) {
        el = document.createElement('div');
        el.id = 'now-line';
        el.hidden = true;
        const label = document.createElement('span');
        label.className = 'now-line-label';
        label.textContent = '--:--';
        el.appendChild(label);
        main.appendChild(el);
      } else if (el.parentElement !== main) {
        el.remove();
        main.appendChild(el);
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
      this._tick(true); // immediat, sans smooth
      this._timer = setInterval(()=>this._tick(false), this._intervalMs);
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

    _tick(instant){
      const container = document.querySelector('.timeline-container');
      if (!container || !this._lineEl) return;
      const scrollBehavior = instant ? 'instant' : 'smooth';

      // Offset du container par rapport a #timeline-main (hauteur day-nav-bar)
      const _mainEl = document.getElementById('timeline-main');
      const _contOff = _mainEl ? (container.getBoundingClientRect().top - _mainEl.getBoundingClientRect().top) : 0;

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

      // --- Section cible : aujourd'hui > sinon fixer en haut/bas ---
      let target = map.find(x => x.iso === nowYMD);
      const todayFound = !!target;
      this._lineEl.style.display = '';

      if (!todayFound) {
        const allBefore = map.every(x => x.iso < nowYMD);
        const allAfter  = map.every(x => x.iso > nowYMD);

        if (allBefore) {
          const lastSection = map[map.length - 1].el;
          const lastCards = Array.from(lastSection.querySelectorAll('.event-item'));
          const lastEl = lastCards.length ? lastCards[lastCards.length - 1] : lastSection;
          const contRect = container.getBoundingClientRect();
          const elRect = lastEl.getBoundingClientRect();
          const y = elRect.bottom - contRect.top + container.scrollTop + 4;
          this._lineEl.style.top = (y + _contOff) + 'px';
          container.scrollTo({ top: Math.max(0, y - container.clientHeight + 40), behavior: scrollBehavior });
        } else if (allAfter) {
          const firstSection = map[0].el;
          const contRect = container.getBoundingClientRect();
          const secRect = firstSection.getBoundingClientRect();
          const y = secRect.top - contRect.top + container.scrollTop - 16;
          this._lineEl.style.top = Math.max(_contOff, y + _contOff) + 'px';
          container.scrollTo({ top: 0, behavior: scrollBehavior });
        } else {
          const past = map.filter(x => x.iso < nowYMD);
          const lastPast = past[past.length - 1].el;
          const contRect = container.getBoundingClientRect();
          const secRect = lastPast.getBoundingClientRect();
          const y = secRect.bottom - contRect.top + container.scrollTop + 4;
          this._lineEl.style.top = (y + _contOff) + 'px';
          container.scrollTo({ top: Math.max(0, y - this._lineTopPx), behavior: scrollBehavior });
        }
        return;
      }

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
        let lineTop = Math.max(_contOff, Math.min(_contOff + container.clientHeight - 2, y + _contOff));
        this._lineEl.style.top = `${lineTop}px`;
      };

      // --- Cas sans carte exploitable : caler sur le haut de section
      if (!cards.length) {
        const secRect = targetSection.getBoundingClientRect();
        const sectionAbsTop = currentScroll + (secRect.top - contRect.top);

        const desiredScrollTop = Math.max(0, sectionAbsTop - this._lineTopPx);
        const clamped = Math.max(0, Math.min(desiredScrollTop, maxScroll));
        container.scrollTo({ top: clamped, behavior: scrollBehavior });

        if (clamped !== desiredScrollTop) {
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
        // Chercher la premiere carte APRES maintenant
        const next = cards.find(c => c.minute >= nowMin);
        if (next) {
          minuteTarget = next.minute;
        } else {
          // Toutes les cartes sont dans le passe -> derniere carte
          minuteTarget = cards[cards.length-1].minute;
        }
      } else if (targetISO < nowYMD) {
        minuteTarget = cards[cards.length-1].minute;
      } else {
        minuteTarget = cards[0].minute;
      }

      // --- Carte pivot = prochaine carte future ---
      const pivot = cards.find(c => c.minute >= minuteTarget) || cards[cards.length-1];

      // --- Aujourd'hui : positionner la ligne AU-DESSUS de la carte pivot ---
      // avec un offset proportionnel au temps restant (max 60px)
      let pivotOffset = 0;
      if (targetISO === nowYMD && pivot.minute > nowMin) {
        // Plus on est loin du prochain evenement, plus l'offset est grand (cap 60px)
        const gap = pivot.minute - nowMin;
        pivotOffset = Math.min(60, Math.max(20, gap * 0.3));
      }

      // --- Position absolue du top de la carte pivot ---
      const cardRect = pivot.el.getBoundingClientRect();
      const pivotAbsTop = currentScroll + (cardRect.top - contRect.top) - pivotOffset;

      // On veut mettre la ligne a _lineTopPx
      const desiredScrollTop = Math.max(0, pivotAbsTop - this._lineTopPx);
      const clamped = Math.max(0, Math.min(desiredScrollTop, maxScroll));
      container.scrollTo({ top: clamped, behavior: scrollBehavior });

      // --- Gestion des butees ---
      if (clamped !== desiredScrollTop) {
        const bottomSafe = 8;
        const topSafe    = 8;

        if (clamped === maxScroll) {
          // Butee BAS : placer la ligne au-dessus de la carte pivot
          const pivotTopViewportY = cardRect.top - contRect.top - pivotOffset;
          const y = Math.min(container.clientHeight - bottomSafe, pivotTopViewportY);
          placeLineViewportY(y);
        } else if (clamped === 0) {
          const firstCard = cards[0].el;
          const firstRect = firstCard.getBoundingClientRect();
          const firstTopViewportY = firstRect.top - contRect.top - pivotOffset;
          const y = Math.max(topSafe, firstTopViewportY);
          placeLineViewportY(y);
        } else {
          const pivotTopViewportY = cardRect.top - contRect.top - pivotOffset;
          placeLineViewportY(pivotTopViewportY);
        }
      } else {
        placeLineViewportY(this._lineTopPx);
      }

      console.info('[NowLine] tick', {
        section: targetISO,
        nowMin,
        minuteTarget,
        pivotMinute: pivot.minute,
        pivotOffset,
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

/* ============================================================
 * RECHERCHE TIMELINE
 *  - construit un index au rendu (items et items dans clusters)
 *  - filtre sur activity/category/place/remark
 *  - clic résultat -> scroll vers la carte (ou cluster + sous-ligne)
 * ============================================================ */

(function(){
  // index en mémoire
  const TLIndex = {
    // { id, date, minute, kind:'item'|'cluster-child', title, category, place, remark, el, clusterEl?, subLi? }
    rows: [],
    mapById: new Map(),
    reset(){ this.rows = []; this.mapById = new Map(); },
    add(row){
      this.rows.push(row);
      if (row.id) this.mapById.set(String(row.id), row);
    },
    get(id){ return this.mapById.get(String(id)); }
  };
  window.__TLIndex = TLIndex; // utile au debug

  // Normalisation (réutilise ta norm)
  function N(s){ return norm(s || ''); }

  // Debounce utilitaire
  function debounce(fn, wait=200){
    let t; return (...args)=>{ clearTimeout(t); t = setTimeout(()=>fn(...args), wait); };
  }

  // --- Scroll & highlight
  function scrollToTimelineTarget(row){
    const container = document.querySelector('.timeline-container');
    if (!container || !row) return;

    // On cible l'élément principal à mettre sous la "ligne" top (comme NowLineController)
    let targetEl = row.el || row.clusterEl;
    if (!targetEl) return;

    // Si c'est un cluster-child, on ouvre le cluster pour révéler la sous-ligne
    if (row.kind === 'cluster-child' && row.clusterEl) {
      // ouvrir si non ouvert
      if (!row.clusterEl.classList.contains('expanded')) {
        row.clusterEl.classList.add('expanded');
        const icon = row.clusterEl.querySelector('.expand-btn .material-icons');
        if (icon) icon.textContent = 'expand_less';
      }
      // petit délai pour que la sous-ligne existe bien en layout
      setTimeout(()=> {
        try { row.subLi?.scrollIntoView({ block:'center', behavior:'smooth' }); } catch(e){}
        row.subLi?.classList.add('search-highlight');
        setTimeout(()=> row.subLi?.classList.remove('search-highlight'), 1400);
      }, 40);
    }

    // Calcul scrollTop pour placer la carte vers ~100px du haut
    const contRect = container.getBoundingClientRect();
    const cardRect = targetEl.getBoundingClientRect();
    const currentScroll = container.scrollTop;
    const yAbs = currentScroll + (cardRect.top - contRect.top);
    const lineTop = (window.NowLineController?._lineTopPx ?? 100);
    const desired = Math.max(0, yAbs - lineTop);
    const maxScroll = container.scrollHeight - container.clientHeight;
    const clamped = Math.max(0, Math.min(desired, maxScroll));
    container.scrollTo({ top: clamped, behavior: 'smooth' });

    // effet highlight court sur la carte/cluster
    targetEl.classList.add('search-highlight');
    setTimeout(()=> targetEl.classList.remove('search-highlight'), 1200);
  }

  // --- Rendu résultats
  function renderResults(list){
    const ul = document.getElementById('timeline-search-results');
    if (!ul) return;
    ul.innerHTML = '';
    if (!list.length) { ul.classList.remove('show'); return; }

    // Limite d'affichage
    const MAX = 30;
    const sliced = list.slice(0, MAX);

    sliced.forEach(row => {
      const li = document.createElement('li');
      li.setAttribute('role', 'option');
      const title = row.title || 'Sans titre';
      const meta  = [row.place, row.category].filter(Boolean).join(' • ');
      li.innerHTML = `
        <div>
          <div class="tsr-title">${title}</div>
          <div class="tsr-meta">${meta}${row.remark ? ' • ' + row.remark : ''}</div>
        </div>
        <div class="tsr-date">${row.date}</div>
      `;
      li.addEventListener('click', () => {
        ul.classList.remove('show');
        scrollToTimelineTarget(row);
      });
      ul.appendChild(li);
    });

    ul.classList.add('show');
  }

  // --- Moteur de recherche
  function searchIndex(q){
    const qry = N(q).trim();
    if (!qry) return [];
    const toks = qry.split(/\s+/).filter(Boolean);
    if (!toks.length) return [];

    const activeDept = (document.getElementById('timeline-dept-filter')?.value || '').trim().toLowerCase();

    // match: toutes les tokens doivent être trouvées dans le blob
    const matches = [];
    for (const r of TLIndex.rows) {
      if (activeDept && (r.department || '').trim().toLowerCase() !== activeDept) continue;
      const blob = N([r.title, r.category, r.place, r.remark].filter(Boolean).join(' | '));
      const ok = toks.every(t => blob.includes(t));
      if (ok) matches.push(r);
    }

    // ordre: date asc puis minute asc puis titre
    matches.sort((a,b)=>{
      if (a.date !== b.date) return a.date.localeCompare(b.date);
      const ma = Number.isFinite(a.minute) ? a.minute : 999999;
      const mb = Number.isFinite(b.minute) ? b.minute : 999999;
      if (ma !== mb) return ma - mb;
      return (a.title||'').localeCompare(b.title||'');
    });
    return matches;
  }

  // --- UI handlers
  function initTimelineSearchUI(){
    const input = document.getElementById('timeline-search-input');
    const clear = document.getElementById('timeline-search-clear');
    const list  = document.getElementById('timeline-search-results');
    if (!input || !clear || !list) return;

    const doSearch = debounce(()=>{
      if (window.CockpitMapView && window.CockpitMapView.currentView && window.CockpitMapView.currentView() === "map") return;
      const q = input.value || '';
      const res = searchIndex(q);
      renderResults(res);
    }, 140);

    input.addEventListener('input', doSearch);
    input.addEventListener('keydown', (e)=>{
      if (window.CockpitMapView && window.CockpitMapView.currentView && window.CockpitMapView.currentView() === "map") return;
      const items = list.querySelectorAll('li');
      const active = list.querySelector('li.active');
      let idx = -1;
      if (active) { items.forEach((li, i) => { if (li === active) idx = i; }); }

      if (e.key === 'ArrowDown') {
        e.preventDefault();
        if (active) active.classList.remove('active');
        idx = (idx + 1) % items.length;
        if (items[idx]) { items[idx].classList.add('active'); items[idx].scrollIntoView({ block: 'nearest' }); }
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        if (active) active.classList.remove('active');
        idx = idx <= 0 ? items.length - 1 : idx - 1;
        if (items[idx]) { items[idx].classList.add('active'); items[idx].scrollIntoView({ block: 'nearest' }); }
      } else if (e.key === 'Enter') {
        e.preventDefault();
        if (active) {
          active.click();
        } else {
          const res = searchIndex(input.value || '');
          if (res.length) {
            list.classList.remove('show');
            scrollToTimelineTarget(res[0]);
          }
        }
      } else if (e.key === 'Escape') {
        input.value = '';
        list.classList.remove('show');
      }
    });
    clear.addEventListener('click', ()=>{
      if (window.CockpitMapView && window.CockpitMapView.currentView && window.CockpitMapView.currentView() === "map") return;
      input.value = '';
      list.classList.remove('show');
      input.focus();
    });

    // === Brancher le filtre Département ===
    const sel = document.getElementById('timeline-dept-filter');
    if (sel && !sel._wired) {
      sel.addEventListener('change', () => {
        applyDeptFilter(sel.value || '');
        // si une recherche est en cours, rafraîchir la liste
        if (input && input.value.trim()) {
          const res = searchIndex(input.value);
          renderResults(res);
        }
      });
      sel._wired = true; // évite de brancher deux fois
    }

    // clic hors pour fermer
    document.addEventListener('click', (e)=>{
      if (!e.target.closest('#timeline-searchbar')) list.classList.remove('show');
    });
  }

  // --- Hook d'indexation : on “patche” fetchTimetable pour remplir l'index après rendu
  const _origFetchTT = window.fetchTimetable;
  window.fetchTimetable = function(){
    const p = _origFetchTT.apply(this, arguments);
    return Promise.resolve(p)
      .then(() => new Promise(r => setTimeout(r, 0)))  // ← laisse le DOM se peindre
      .then(()=> {
        // (ré)indexer
        TLIndex.reset();

        // 1) Items simples
        document.querySelectorAll('.timetable-date-section').forEach(section=>{
          const date = section.dataset.date || '';
          section.querySelectorAll('.event-item').forEach(card=>{
            // item individuel ?
            if (card.__itemData) {
              const it = card.__itemData;
              TLIndex.add({
                id: it._id,
                date,
                minute: getItemSortMinute(it),
                kind: 'item',
                title: (it.activity || '').split('/')[0].trim(),
                category: it.category || '',
                place: (it.place || '').split('/')[0].trim(),
                department: it.department || '',
                remark: it.remark || '',
                el: card
              });
            }

            // 2) Cluster : indexer chaque sous-ligne comme "cluster-child"
            if (card.__clusterData) {
              const cl = card.__clusterData.cluster;
              const items = cl.items || [];
              card.querySelectorAll('.cluster-line').forEach(li=>{
                const cid = li.getAttribute('data-child-id');
                const ch = items.find(x => String(x._id) === String(cid));
                if (!ch) return;
                TLIndex.add({
                  id: ch._id,
                  date,
                  minute: getItemSortMinute(ch),
                  kind: 'cluster-child',
                  title: (ch.activity || '').split('/')[0].trim(),
                  category: ch.category || '',
                  place: (ch.place || '').split('/')[0].trim(),
                  remark: ch.remark || '',
                  department: ch.department || '',
                  el: card,             // pour le highlight cluster
                  clusterEl: card,      // carte cluster
                  subLi: li             // sous-ligne à surligner
                });
              });
            }
          });
        });

        // Initialiser l'UI au premier rendu
        initTimelineSearchUI();
        populateDeptFilter();
        applyDeptFilter(document.getElementById('timeline-dept-filter')?.value || '');
    });
  };

  function buildDepartmentListFromIndex() {
    const set = new Set();
    for (const r of TLIndex.rows) {
      const d = (r.department || '').trim();
      if (d) set.add(d);
    }
    return Array.from(set).sort((a,b)=> a.localeCompare(b, 'fr', { numeric:true, sensitivity:'base' }));
  }

  function populateDeptFilter() {
    const sel = document.getElementById('timeline-dept-filter');
    if (!sel) return;
    const prev = sel.value || '';
    const list = buildDepartmentListFromIndex();

    sel.innerHTML = '<option value="">Tous départements</option>' +
      list.map(d => `<option value="${d}">${d}</option>`).join('');

    if (prev && Array.from(sel.options).some(o => o.value === prev)) {
      sel.value = prev;
    }
  }

  function matchesDeptVal(depValue, selected) {
    if (!selected) return true;
    return (depValue || '').trim().toLowerCase() === selected.trim().toLowerCase();
  }

  function applyDeptFilter(selected) {
    const container = document.querySelector('.timeline-container');
    if (!container) return;

    // Parcourt chaque section (jour)
    container.querySelectorAll('.timetable-date-section').forEach(section => {
      let sectionHasVisible = false;

      section.querySelectorAll('.event-item').forEach(card => {
        let show = true;

        if (card.__itemData) {
          // Carte d'item individuel
          show = matchesDeptVal(card.__itemData.department, selected);
        } else if (card.__clusterData) {
          // Carte de cluster: visible si au moins un enfant matche
          const cl = card.__clusterData.cluster;
          show = (cl.items || []).some(ch => matchesDeptVal(ch.department, selected));
        }

        card.style.display = show ? '' : 'none';
        if (show) sectionHasVisible = true;
      });

      // Cache la section entière si elle ne contient rien de visible
      section.style.display = sectionHasVisible ? '' : 'none';
    });

    // Cache aussi la liste de suggestions si un filtre vient d'être appliqué
    document.getElementById('timeline-search-results')?.classList.remove('show');
  }

  // Peut-on marquer "Prête" ? (OK si aucune tâche, ou si toutes cochées)
  function canMarkReadyFromTodos(item){
    const tasks = splitTodo(item.todo || "");
    if (!tasks.length) return true;
    return tasks.every(t => !!t.done);
  }

  // Libellé FR pour un statut
  function getPrepLabelShort(s) {
    return s === 'true' ? 'Prête'
        : s === 'progress' ? 'En cours'
        : 'Non';
  }

  // Met à jour l'état visuel des boutons + pastille dans le drawer
  function updatePrepControls(containerEl, item){
    if (!containerEl || !item) return;
    const group = containerEl.querySelector('.prep-status-group');
    if (!group) return;

    const cur = (item.preparation_checked ?? '').toString().toLowerCase() || 'none';
    group.querySelectorAll('[data-status]').forEach(btn=>{
      const v = btn.getAttribute('data-status');
      btn.classList.toggle('active', v === cur);
    });

    // Gère le bouton "Prête" (disable + tooltip si tâches incomplètes)
    const btnReady = group.querySelector('[data-status="true"]');
    if (btnReady) {
      const allowed = canMarkReadyFromTodos(item);
      btnReady.disabled = !allowed;
      btnReady.title = allowed
        ? 'Marquer comme prête'
        : "Impossible : des tâches TODO ne sont pas cochées";
    }

    // Met à jour la pastille du drawer (visuel)
    const pill = containerEl.querySelector('.prep-pill');
    if (pill) {
      const clsBase = 'prep-pill';
      const cls = (cur === 'true' ? 'prep-true' : (cur === 'progress' ? 'prep-progress' : 'prep-none'));
      pill.className = `${clsBase} ${cls}`;
      pill.textContent = getPrepLabelShort(cur);
    }
  }

  function setPrepStatusFromDrawer(dateStr, item, newStatus) {
    if (!item) return;
  
    // -- ID robuste (_id | id | fallback depuis le drawer) --
    const getEventId = (it) => {
      const v = it?._id ?? it?.id ?? (window.drawerEl?.dataset?.eventId) ?? '';
      return v ? String(v) : '';
    };
    const evId = getEventId(item);
    if (!evId) {
      typeof showDynamicFlashMessage === 'function' &&
        showDynamicFlashMessage("ID manquant pour cet élément.", "error");
      return;
    }
  
    // -- Contexte requis --
    if (!window.selectedEvent || !window.selectedYear || !dateStr) {
      typeof showDynamicFlashMessage === 'function' &&
        showDynamicFlashMessage("Contexte incomplet (event/year/date).", "error");
      return;
    }
  
    // -- Normalisation du statut demandé --
    const norm = (s) => (s ?? '').toString().trim().toLowerCase();
    const current = norm(item.preparation_checked);
    let target = norm(newStatus);
  
    // accepter quelques alias
    if (target === 'none' || target === 'non' || target === 'no' || target === 'false' || target === 'pending') target = '';
    if (target === 'ready' || target === 'ok' || target === 'prête' || target === 'prete') target = 'true';
    if (target === 'en cours' || target === 'inprogress') target = 'progress';
  
    if (current === target) return; // rien à faire
  
    // -- Règle métier : pas de "true" si TODO non cochées --
    if (target === 'true' && !canMarkReadyFromTodos(item)) {
      typeof showDynamicFlashMessage === 'function' &&
        showDynamicFlashMessage("Des tâches TODO ne sont pas cochées — impossible de marquer 'Prête'.", "warning");
      return;
    }
  
    // -- Mise à jour optimiste locale (pour le rendu immédiat) --
    item.preparation_checked = target;
  
    // -- util POST --
    const csrf = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';
    const postJSON = (url, payload) =>
      fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf },
        body: JSON.stringify(payload)
      });
  
    // -- après chaque requête : refresh + re-render --
    const doAfter = () => {
      try { fetchTimetable(); } catch(e){}
      try { renderDrawerView(); } catch(e){}
    };
  
    // -- Routage selon le statut cible --
    if (target === 'true') {
      // Prête → route dédiée (app.py attend {event,year,date,id})
      postJSON('/set_preparation_ready', {
        id: evId,
        event: window.selectedEvent,
        year: window.selectedYear,
        date: dateStr
      }).then(doAfter).catch(doAfter);
  
    } else if (target === 'progress') {
      // En cours → route dédiée (app.py attend {event,year,date,id})
      postJSON('/set_preparation_progress', {
        id: evId,
        event: window.selectedEvent,
        year: window.selectedYear,
        date: dateStr
      }).then(doAfter).catch(doAfter);
  
    } else {
      // Non ("") → passer par update_timetable_event
      // IMPORTANT: envoyer null pour tous les autres champs pour ne PAS les écraser
      postJSON('/update_timetable_event', {
        event: window.selectedEvent,
        year: window.selectedYear,
        date: dateStr,
        _id: evId,
        preparation_checked: "",
        start: null, end: null, duration: null,
        category: null, activity: null, place: null,
        department: null, remark: null, todo: null
      }).then(doAfter).catch(doAfter);
    }
  }  

  window.canMarkReadyFromTodos   = canMarkReadyFromTodos;
  window.updatePrepControls      = updatePrepControls;
  window.setPrepStatusFromDrawer = setPrepStatusFromDrawer;

})();

// ============================================================================
// Polling version timetable : detecte les merges declenches par groundmaster
// (webhook /webhook/parametrage-updated) et rafraichit la timeline sans reload.
// ============================================================================
(function () {
  var POLL_MS = 30000;
  var inFlight = false;

  async function pollOnce() {
    if (inFlight) return;
    if (document.hidden) return;
    if (!window.selectedEvent || !window.selectedYear) return;
    if (typeof window.fetchTimetable !== 'function') return;
    inFlight = true;
    try {
      var url = '/api/timetable/version?event=' + encodeURIComponent(window.selectedEvent)
              + '&year=' + encodeURIComponent(window.selectedYear);
      var resp = await fetch(url, { credentials: 'same-origin' });
      if (!resp.ok) return;
      var json = await resp.json();
      var remote = (json && typeof json.version === 'number') ? json.version : null;
      if (remote === null) return;
      var local = window._timetableVersion;
      if (typeof local !== 'number') {
        window._timetableVersion = remote;
        return;
      }
      if (remote !== local) {
        window._timetableVersion = remote;
        window.fetchTimetable();
      }
    } catch (e) {
      // silencieux : reseau/serveur indisponible ne doit pas casser l'UI
    } finally {
      inFlight = false;
    }
  }

  if (window._timetableVersionPollTimer) clearInterval(window._timetableVersionPollTimer);
  window._timetableVersionPollTimer = setInterval(pollOnce, POLL_MS);
})();
