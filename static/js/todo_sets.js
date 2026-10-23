(function(){
  const $ = (s, r=document)=>r.querySelector(s);
  const $$ = (s, r=document)=>Array.from(r.querySelectorAll(s));

  const API = {
    list: (params={}) => fetch('/api/todo-sets' + toQuery(params)).then(r=>r.json()),
    get: (id) => fetch('/api/todo-sets/'+id).then(r=>r.json()),
    create: (payload) => fetch('/api/todo-sets', { method:'POST', headers:jsonHeaders(), body:JSON.stringify(payload)}).then(r=>r.json()),
    update: (id, payload) => fetch('/api/todo-sets/'+id, { method:'PUT', headers:jsonHeaders(), body:JSON.stringify(payload)}).then(r=>r.json()),
    remove: (id) => fetch('/api/todo-sets/'+id, { method:'DELETE' }).then(r=>r.json()),
    bulkRemove: (ids) => fetch('/api/todo-sets/bulk-delete', { method:'POST', headers:jsonHeaders(), body:JSON.stringify({ids}) }).then(r=>r.json()),
  };

  function jsonHeaders(){
    const h={'Content-Type':'application/json'};
    const t=getCSRF(); if(t) h['X-CSRFToken']=t; return h;
  }
  function getCSRF(){ const m=$('meta[name="csrf-token"]'); return m?m.getAttribute('content'):''; }
  function toQuery(p){
    const sp=new URLSearchParams();
    Object.entries(p).forEach(([k,v])=>{ if(v!==''&&v!=null) sp.set(k,v) });
    const s=sp.toString(); return s?('?'+s):'';
  }

  const tbody = $('#todo-sets-table tbody');
  const checkAll = $('#check-all');
  const btnAdd = $('#btn-add');
  const btnDeleteSel = $('#btn-delete-selected');

  const modal = $('#todo-set-modal');
  const modalTitle = $('#modal-title');
  const form = $('#todo-set-form');
  const btnSave = $('#modal-save');

  let current = [];

  function openModal(){ modal.removeAttribute('hidden'); }
  function closeModal(){ modal.setAttribute('hidden',''); }
  $$("[data-close]", modal).forEach(b=> b.addEventListener('click', closeModal));
  modal.addEventListener('click', (e)=>{ if(e.target===modal) closeModal(); });

  // ----- dynamic todo items -----

  // Le mode d'acces (controle/libre/both) ne concerne que ouverture/fermeture.
  // Les bascules (switch_*) encodent deja la transition de mode.
  function _phaseHasControl(phase) {
    return phase === 'open' || phase === 'close' || phase === 'both';
  }

  // item (optionnel) : {id, critical, due_offset_min, origin}. L'id est
  // renvoye tel quel au serveur : il rattache les taches deja creees sur les
  // vignettes (etat, auteur) a l'item, meme apres reformulation.
  function _addItemRow(listEl, text, control, item) {
    const phase = (listEl.id || '').replace('todo-list-', '');
    const withControl = _phaseHasControl(phase);
    item = item || {};

    const row = document.createElement('div');
    row.className = 'todo-item-row';
    row.dataset.id = item.id || '';
    row.dataset.origin = item.origin || '';

    const input = document.createElement('input');
    input.type = 'text';
    input.value = text || '';
    input.placeholder = 'Nouvelle tache...';
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        // Ajouter une ligne apres celle-ci si l'input n'est pas vide
        if (input.value.trim()) {
          const newRow = _addItemRow(listEl, '');
          row.after(newRow);
          newRow.querySelector('input[type="text"]').focus();
        }
      }
    });

    row.appendChild(input);

    if (withControl) {
      const sel = document.createElement('select');
      sel.className = 'todo-item-control';
      sel.title = "Mode d'acces cible (l'item ouvre en controle ou libre)";
      sel.innerHTML = '<option value="both">Tous</option>'
                    + '<option value="controle">Contrôlé</option>'
                    + '<option value="libre">Libre</option>';
      sel.value = (control === 'controle' || control === 'libre') ? control : 'both';
      row.appendChild(sel);
    }

    // Echeance : minutes avant le debut de la vignette (vide = au debut)
    const due = document.createElement('input');
    due.type = 'number';
    due.className = 'todo-item-due';
    due.step = '5';
    due.min = '-2880';
    due.max = '2880';
    due.placeholder = 'min';
    due.title = "Echeance : minutes AVANT le debut de la vignette (vide = au debut, negatif = apres). Sert a signaler les retards.";
    if (item.due_offset_min !== null && item.due_offset_min !== undefined && item.due_offset_min !== '') due.value = item.due_offset_min;
    row.appendChild(due);

    const crit = document.createElement('button');
    crit.type = 'button';
    crit.className = 'todo-item-crit' + (item.critical ? ' is-on' : '');
    crit.title = 'Tache critique (mise en avant, signalee au briefing)';
    crit.setAttribute('aria-pressed', item.critical ? 'true' : 'false');
    crit.innerHTML = '<span class="material-symbols-outlined" style="font-size:16px;">priority_high</span>';
    crit.addEventListener('click', () => {
      const on = !crit.classList.contains('is-on');
      crit.classList.toggle('is-on', on);
      crit.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
    row.appendChild(crit);

    const removeBtn = document.createElement('button');
    removeBtn.type = 'button';
    removeBtn.className = 'todo-item-remove';
    removeBtn.title = 'Supprimer';
    removeBtn.innerHTML = '<span class="material-symbols-outlined" style="font-size:16px;">close</span>';
    removeBtn.addEventListener('click', () => {
      row.remove();
    });

    row.appendChild(removeBtn);
    listEl.appendChild(row);
    return row;
  }

  function _fillPhaseList(phase, items) {
    const listEl = $('#todo-list-' + phase);
    listEl.innerHTML = '';
    items.forEach(it => _addItemRow(listEl, it.text, it.control, it));
  }

  function _collectPhaseItems(phase) {
    const listEl = $('#todo-list-' + phase);
    if (!listEl) return [];
    return $$('.todo-item-row', listEl).map(row => {
      const text = (row.querySelector('input[type="text"]')?.value || '').trim();
      if (!text) return null;
      const ctrlSel = row.querySelector('.todo-item-control');
      const control = ctrlSel ? ctrlSel.value : 'both';
      const dueRaw = (row.querySelector('.todo-item-due')?.value || '').trim();
      return {
        id: row.dataset.id || undefined,
        text, phase, control,
        critical: !!row.querySelector('.todo-item-crit.is-on'),
        due_offset_min: dueRaw === '' ? null : Number(dueRaw),
        origin: row.dataset.origin || '',
      };
    }).filter(Boolean);
  }

  // boutons "+ Ajouter"
  $$('.todo-add-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const phase = btn.dataset.phase;
      const listEl = $('#todo-list-' + phase);
      const row = _addItemRow(listEl, '');
      row.querySelector('input').focus();
    });
  });

  // ----- form <-> payload -----

  function formToPayload(){
    return {
      type: (form.elements.type.value || '').trim(),
      todos: [
        ..._collectPhaseItems('open'),
        ..._collectPhaseItems('close'),
        ..._collectPhaseItems('both'),
        ..._collectPhaseItems('switch_control'),
        ..._collectPhaseItems('switch_free'),
      ]
    };
  }

  function _itemsByPhase(todos, phase) {
    return (todos || [])
      .filter(t => {
        if (typeof t === 'string') return phase === 'open';
        return (t.phase || 'open') === phase;
      })
      .map(t => typeof t === 'string'
        ? { text: t, control: 'both' }
        : Object.assign({}, t, { control: t.control || 'both' }));
  }

  function payloadToForm(doc){
    form.reset();
    form.elements._id.value = doc._id || '';
    form.elements.type.value = doc.type || '';
    _fillPhaseList('open', _itemsByPhase(doc.todos, 'open'));
    _fillPhaseList('close', _itemsByPhase(doc.todos, 'close'));
    _fillPhaseList('both', _itemsByPhase(doc.todos, 'both'));
    _fillPhaseList('switch_control', _itemsByPhase(doc.todos, 'switch_control'));
    _fillPhaseList('switch_free', _itemsByPhase(doc.todos, 'switch_free'));
  }

  // ----- table rendering -----

  function renderRows(list){
    const rows = (list || []).map(renderRow).join('');
    tbody.innerHTML = rows || '<tr><td colspan="4" class="muted">Aucun resultat</td></tr>';
  }

  const _phaseIcon = {
    open: 'lock_open', close: 'lock', both: 'sync',
    switch_control: 'shield_lock', switch_free: 'lock_open_right'
  };
  const _phaseColor = {
    open: 'var(--success)', close: 'var(--danger)', both: 'var(--accent)',
    switch_control: 'var(--warning)', switch_free: 'var(--success)'
  };

  function _todoText(t) {
    return typeof t === 'string' ? t : (t.text || '');
  }
  function _todoPhase(t) {
    return typeof t === 'string' ? 'open' : (t.phase || 'open');
  }
  function _todoControl(t) {
    return typeof t === 'string' ? 'both' : (t.control || 'both');
  }
  const _controlIcon = { controle: 'shield_lock', libre: 'lock_open_right' };
  const _controlColor = { controle: '#9a3412', libre: '#166534' };

  function renderRow(d){
    const items = (d.todos || []).slice(0, 4);
    const preview = items.map(t => {
      const ph = _todoPhase(t);
      const icon = _phaseIcon[ph] || 'sync';
      const color = _phaseColor[ph] || 'var(--muted)';
      const ctrl = _todoControl(t);
      const ctrlHtml = (ctrl === 'controle' || ctrl === 'libre')
        ? '<span class="material-symbols-outlined" style="font-size:12px;color:' + _controlColor[ctrl] + ';" title="' + ctrl + '">' + _controlIcon[ctrl] + '</span>'
        : '';
      return '<span style="display:inline-flex;align-items:center;gap:2px;">'
        + '<span class="material-symbols-outlined" style="font-size:13px;color:' + color + ';">' + icon + '</span>'
        + escapeHtml(_todoText(t))
        + ctrlHtml
        + '</span>';
    }).join(' <span style="color:var(--muted);">&#8226;</span> ');
    const more = (d.todos||[]).length > 4 ? ' <span class="muted">(+' + ((d.todos.length - 4)) + ')</span>' : '';
    return '<tr data-id="' + d._id + '">'
      + '<td><input type="checkbox" class="row-check"></td>'
      + '<td><strong>' + escapeHtml(d.type||'') + '</strong></td>'
      + '<td>' + preview + more + '</td>'
      + '<td class="group-actions">'
      +   '<button class="btn-icon" data-action="insights" title="Usage reel, RETEX et suggestions IA"><span class="material-symbols-outlined">insights</span></button>'
      +   '<button class="btn-icon" data-action="edit" title="Editer"><span class="material-symbols-outlined">edit</span></button>'
      +   '<button class="btn-icon btn-icon-danger" data-action="delete" title="Supprimer"><span class="material-symbols-outlined">delete</span></button>'
      + '</td></tr>';
  }

  function escapeHtml(s){
    return (s||'').replace(/[&<>"']/g, c=>({
      '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
    })[c]);
  }

  async function refresh(){
    const list = await API.list();
    current = list;
    renderRows(list);
  }

  btnAdd.addEventListener('click', ()=>{
    modalTitle.textContent = 'Nouveau type';
    payloadToForm({type:'', todos:[]});
    openModal();
  });

  function _autoMerge() {
    const event = window.selectedEvent;
    const year = window.selectedYear;
    if (!event || !year) return;
    fetch('/api/run-merge', {
      method: 'POST',
      headers: jsonHeaders(),
      body: JSON.stringify({ event: event, year: year }),
    })
    .then(r => r.json())
    .then(data => {
      if (data.ok) {
        showToast("success", "Vignettes regenerees (" + data.vignettes_count + ")");
      }
    })
    .catch(() => {});
  }

  btnSave.addEventListener('click', async ()=>{
    const payload = formToPayload();
    const id = form.elements._id.value;
    if(!payload.type){ showToast("warning", "Le type est requis."); return; }
    if(id){ await API.update(id, payload); }
    else {
      const exists = current.some(x=> (x.type||'').toLowerCase() === payload.type.toLowerCase());
      if(exists && !(await showConfirmToast("Ce type existe deja. Continuer quand meme ?"))) return;
      await API.create(payload);
    }
    closeModal();
    await refresh();
    _autoMerge();
  });

  tbody.addEventListener('click', async (e)=>{
    const btn = e.target.closest('button'); if(!btn) return;
    const tr = e.target.closest('tr'); const id = tr?.dataset?.id; const act = btn.dataset.action;
    if(act==='edit'){
      const doc = current.find(x=>x._id===id) || await API.get(id);
      modalTitle.textContent = 'Editer la categorie';
      payloadToForm(doc);
      openModal();
    }
    if(act==='insights'){
      const doc = current.find(x=>x._id===id) || await API.get(id);
      openInsights(doc);
    }
    if(act==='delete'){
      if(await showConfirmToast("Supprimer cette categorie et ses taches ?", { type: "error", okLabel: "Supprimer" })){ await API.remove(id); await refresh(); }
    }
  });

  checkAll.addEventListener('change', ()=>{
    $$('.row-check', tbody).forEach(cb=> cb.checked = checkAll.checked);
  });

  btnDeleteSel.addEventListener('click', async ()=>{
    const ids = $$('.row-check:checked', tbody).map(cb=> cb.closest('tr').dataset.id);
    if(ids.length===0) { showToast("warning", "Aucun element selectionne."); return; }
    if(await showConfirmToast("Supprimer " + ids.length + " categorie(s) ?", { type: "error", okLabel: "Supprimer" })){
      await API.bulkRemove(ids);
      await refresh();
      checkAll.checked = false;
    }
  });

  // ----- Analyse d'un modele : usage reel, RETEX, suggestions IA -----
  // Toute modification passe par le PUT du modele (ids conserves) puis un
  // merge : les vignettes recoivent l'evolution, les ajouts terrain restent.

  const insModal = $('#todo-insights-modal');
  const insTitle = $('#todo-insights-title');
  const insBody = $('#todo-insights-body');
  let ins = null;   // { doc, data, props }

  $$("[data-close]", insModal).forEach(b => b.addEventListener('click', () => insModal.setAttribute('hidden', '')));
  insModal.addEventListener('click', (e) => { if (e.target === insModal) insModal.setAttribute('hidden', ''); });

  const PHASE_LABELS = { open: 'Ouverture', close: 'Fermeture', both: 'Ouverture et fermeture',
                         switch_control: 'Bascule contrôlée', switch_free: 'Retour libre' };

  async function openInsights(doc) {
    insTitle.textContent = 'Analyse du modèle : ' + (doc.type || '');
    insBody.innerHTML = '<p class="muted">Chargement...</p>';
    insModal.removeAttribute('hidden');
    let data = null;
    try { data = await fetch('/api/todo-sets/' + encodeURIComponent(doc._id) + '/insights').then(r => r.json()); } catch (e) {}
    if (!data || data.error) { insBody.innerHTML = '<p>Analyse indisponible.</p>'; return; }
    ins = { doc, data, props: null };
    renderInsights();
  }

  function _pct(n, d) { return d ? Math.round(100 * n / d) + ' %' : '-'; }

  function _itemText(id) {
    const it = (ins.doc.todos || []).find(t => t && t.id === id);
    return it ? it.text : null;
  }

  function renderInsights() {
    const d = ins.data;
    const eds = d.editions || [];
    let h = '';
    h += '<p class="ins-intro">Usage réel du modèle sur toutes les éditions (tâches arrivées à échéance). '
       + 'Les ajouts acceptés ici partent dans le modèle puis sur les vignettes au prochain merge ; les tâches ajoutées sur le terrain ne sont jamais écrasées.</p>';
    h += '<label class="ins-phase">Phase des ajouts : <select id="ins-phase">'
       + Object.keys(PHASE_LABELS).map(k => '<option value="' + k + '"' + (k === 'open' ? ' selected' : '') + '>' + PHASE_LABELS[k] + '</option>').join('')
       + '</select></label>';

    h += '<h4>Réalisation par édition</h4>';
    h += eds.length ? '<table class="table ins-table"><thead><tr><th>Édition</th><th>Traitées</th><th>Échues</th></tr></thead><tbody>'
       + eds.map(e => '<tr><td>' + escapeHtml(e.edition) + '</td><td>' + (!e.suivie ? '<span class="muted" title="Aucune tache de ce type cochee : edition non suivie dans Cockpit, exclue des statistiques par tache">non suivie</span>' : (e.taux == null ? '-' : e.taux + ' %')) + '</td><td>' + e.total + '</td></tr>').join('')
       + '</tbody></table>' : '<p class="muted">Pas encore d\'historique : les chiffres apparaîtront au fil des éditions.</p>';

    const rare = new Set((d.rarement_faites || []).map(i => i.id));
    const late = new Set((d.souvent_en_retard || []).map(i => i.id));
    const items = (d.items || []).filter(i => _itemText(i.id) !== null && i.total > 0);
    if (items.length) {
      h += '<h4>Par tâche du modèle</h4><table class="table ins-table"><thead><tr><th>Tâche</th><th>Faites</th><th>Sans objet</th><th>Non faites</th><th>En retard</th><th></th></tr></thead><tbody>';
      items.forEach(i => {
        let act = '';
        if (rare.has(i.id)) act += '<button class="btn-sm" data-ins="remove" data-id="' + escapeHtml(i.id) + '" title="Presque jamais faite">Retirer</button>';
        if (late.has(i.id)) act += '<button class="btn-sm" data-ins="advance" data-id="' + escapeHtml(i.id) + '" data-min="30" title="Souvent faite en retard">Avancer de 30 min</button>';
        h += '<tr' + (act ? ' class="ins-flag"' : '') + '><td>' + escapeHtml(_itemText(i.id)) + '</td><td>' + _pct(i.faites, i.total) + '</td><td>' + i.sans_objet
           + '</td><td>' + i.non_faites + '</td><td>' + i.faites_en_retard + '</td><td class="ins-acts">' + act + '</td></tr>';
      });
      h += '</tbody></table>';
    }

    const cands = d.candidats_ajout || [];
    h += '<h4>Ajoutées sur le terrain, plusieurs fois</h4>';
    h += cands.length ? '<ul class="ins-list">' + cands.map((c, k) => '<li><div><strong>' + escapeHtml(c.text) + '</strong>'
         + (c.ia ? ' <span class="ins-tag">IA</span>' : '')
         + '<div class="muted">' + escapeHtml(c.editions.join(', ')) + ' · ' + c.vignettes + ' vignette(s)' + (c.lieux.length ? ' · ' + escapeHtml(c.lieux.join(', ')) : '') + '</div></div>'
         + '<button class="btn-sm" data-ins="add" data-src="cand" data-k="' + k + '">Ajouter au modèle</button></li>').join('') + '</ul>'
       : '<p class="muted">Aucun ajout récurrent pour l\'instant.</p>';

    const retex = d.retex || [];
    if (retex.length) {
      h += '<h4>Recommandations des derniers RETEX</h4>';
      retex.forEach((r, ri) => {
        h += '<div class="ins-retex"><div class="muted">' + escapeHtml(r.event + ' ' + r.year) + '</div><ul class="ins-list">'
           + r.bullets.map((b, bi) => '<li><div>' + escapeHtml(b) + '</div><button class="btn-sm" data-ins="add" data-src="retex" data-k="' + ri + ':' + bi + '">En faire une tâche</button></li>').join('')
           + '</ul></div>';
      });
    }

    h += '<h4>Suggestions de l\'IA</h4>';
    if (!ins.props) {
      h += '<p class="muted">L\'IA croise le modèle, son usage réel et les RETEX pour proposer des ajouts, retraits ou reformulations. Appel facturé (budget IA), réservé aux administrateurs.</p>'
         + '<button class="btn btn-secondary" data-ins="ai">Proposer des évolutions</button>';
    } else if (!ins.props.length) {
      h += '<p class="muted">Aucune évolution proposée : le modèle semble cohérent avec l\'usage.</p>';
    } else {
      const LAB = { add: 'Ajouter', remove: 'Retirer', modify: 'Reformuler', advance: 'Avancer l\'échéance' };
      h += '<ul class="ins-list">' + ins.props.map((p, k) => {
        let what = '';
        if (p.action === 'add') what = escapeHtml(p.text) + ' <span class="muted">(' + escapeHtml(PHASE_LABELS[p.phase] || p.phase) + ')</span>';
        else if (p.action === 'remove') what = escapeHtml(_itemText(p.item_id) || '');
        else if (p.action === 'modify') what = '<s>' + escapeHtml(_itemText(p.item_id) || '') + '</s> → ' + escapeHtml(p.text);
        else what = escapeHtml(_itemText(p.item_id) || '') + ' : ' + p.due_offset_min + ' min avant le début';
        return '<li class="' + (p.done ? 'is-done' : '') + '"><div><span class="ins-tag">' + LAB[p.action] + '</span> ' + what
             + (p.reason ? '<div class="muted">' + escapeHtml(p.reason) + '</div>' : '') + '</div>'
             + (p.done ? '<span class="muted">' + escapeHtml(p.done) + '</span>' : '<span class="ins-acts"><button class="btn-sm" data-ins="prop" data-k="' + k + '">Appliquer</button><button class="btn-sm btn-ghost" data-ins="skip" data-k="' + k + '">Ignorer</button></span>')
             + '</li>';
      }).join('') + '</ul>';
    }
    insBody.innerHTML = h;
  }

  async function mutateSet(fn, okMsg) {
    const id = ins.doc._id;
    const doc = await API.get(id);
    const todos = (doc.todos || []).map(t => typeof t === 'string' ? { text: t, phase: 'open' } : Object.assign({}, t));
    if (fn(todos) === false) return false;
    const res = await API.update(id, { todos });
    if (!res || res.error) { showToast('error', (res && res.error) || 'Enregistrement impossible.'); return false; }
    ins.doc = res;
    await refresh();
    _autoMerge();
    showToast('success', okMsg || 'Modèle mis à jour.');
    return true;
  }

  async function addToTemplate(text, phase, origin) {
    const edited = await showPromptToast('Texte de la tâche (action vérifiable, à l\'infinitif) :', { okLabel: 'Ajouter', defaultValue: text });
    if (!edited || !edited.trim()) return false;
    const t = edited.trim().slice(0, 300);
    return mutateSet(todos => {
      if (todos.some(x => (x.text || '').trim().toLowerCase() === t.toLowerCase())) { showToast('warning', 'Cette tâche existe déjà dans le modèle.'); return false; }
      todos.push({ text: t, phase: phase, control: 'both', origin: origin });
    }, 'Tâche ajoutée au modèle.');
  }

  insBody.addEventListener('click', async (e) => {
    const btn = e.target.closest('[data-ins]');
    if (!btn || !ins) return;
    const kind = btn.dataset.ins;
    const phase = ($('#ins-phase') || {}).value || 'open';
    if (kind === 'add') {
      if (btn.dataset.src === 'cand') {
        const c = ins.data.candidats_ajout[Number(btn.dataset.k)];
        if (c) await addToTemplate(c.text, phase, 'historique');
      } else {
        const [ri, bi] = btn.dataset.k.split(':').map(Number);
        const b = ((ins.data.retex[ri] || {}).bullets || [])[bi];
        if (b) await addToTemplate(b, phase, 'retex');
      }
    } else if (kind === 'remove') {
      if (!(await showConfirmToast('Retirer cette tâche du modèle ? Les tâches déjà faites restent dans l\'historique.', { type: 'error', okLabel: 'Retirer' }))) return;
      await mutateSet(todos => { const i = todos.findIndex(t => t.id === btn.dataset.id); if (i < 0) return false; todos.splice(i, 1); }, 'Tâche retirée du modèle.');
    } else if (kind === 'advance') {
      const min = Number(btn.dataset.min || 30);
      await mutateSet(todos => { const t = todos.find(x => x.id === btn.dataset.id); if (!t) return false; t.due_offset_min = (Number(t.due_offset_min) || 0) + min; }, 'Échéance avancée.');
    } else if (kind === 'ai') {
      if (!(await showConfirmToast('Demander des suggestions à l\'IA ? Appel facturé (quelques centimes), 20 à 60 s.', { okLabel: 'Proposer' }))) return;
      btn.disabled = true;
      btn.textContent = 'Analyse en cours...';
      try {
        const r = await fetch('/api/todo-sets/' + encodeURIComponent(ins.doc._id) + '/ai-suggest', { method: 'POST', headers: jsonHeaders(), body: '{}' });
        const data = await r.json().catch(() => ({}));
        if (!r.ok || !data.ok) {
          const msg = data.error === 'budget_exceeded' ? 'Budget IA mensuel dépassé.' : (r.status === 403 ? 'Réservé aux administrateurs.' : 'Service IA indisponible (' + (data.error || r.status) + ').');
          showToast('error', msg);
          btn.disabled = false; btn.textContent = 'Proposer des évolutions';
          return;
        }
        ins.props = data.propositions || [];
        renderInsights();
      } catch (err) {
        showToast('error', 'Service IA injoignable.');
        btn.disabled = false; btn.textContent = 'Proposer des évolutions';
      }
    } else if (kind === 'prop' || kind === 'skip') {
      const p = ins.props[Number(btn.dataset.k)];
      if (!p) return;
      if (kind === 'skip') { p.done = 'Ignorée'; renderInsights(); return; }
      let ok = false;
      if (p.action === 'add') ok = await addToTemplate(p.text, p.phase || phase, 'ia');
      else if (p.action === 'remove') ok = await mutateSet(todos => { const i = todos.findIndex(t => t.id === p.item_id); if (i < 0) return false; todos.splice(i, 1); });
      else if (p.action === 'modify') ok = await mutateSet(todos => { const t = todos.find(x => x.id === p.item_id); if (!t) return false; t.text = p.text; });
      else if (p.action === 'advance') ok = await mutateSet(todos => { const t = todos.find(x => x.id === p.item_id); if (!t) return false; t.due_offset_min = p.due_offset_min; });
      if (ok) { p.done = 'Appliquée'; renderInsights(); }
    }
  });

  refresh();
})();
