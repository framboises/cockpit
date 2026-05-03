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
  const filterType = $('#filter-type');
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

  function _addItemRow(listEl, text) {
    const row = document.createElement('div');
    row.className = 'todo-item-row';

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
          newRow.querySelector('input').focus();
        }
      }
    });

    const removeBtn = document.createElement('button');
    removeBtn.type = 'button';
    removeBtn.className = 'todo-item-remove';
    removeBtn.title = 'Supprimer';
    removeBtn.innerHTML = '<span class="material-symbols-outlined" style="font-size:16px;">close</span>';
    removeBtn.addEventListener('click', () => {
      row.remove();
    });

    row.appendChild(input);
    row.appendChild(removeBtn);
    listEl.appendChild(row);
    return row;
  }

  function _fillPhaseList(phase, items) {
    const listEl = $('#todo-list-' + phase);
    listEl.innerHTML = '';
    items.forEach(text => _addItemRow(listEl, text));
  }

  function _collectPhaseItems(phase) {
    const listEl = $('#todo-list-' + phase);
    if (!listEl) return [];
    return $$('input[type="text"]', listEl)
      .map(i => i.value.trim())
      .filter(Boolean)
      .map(text => ({ text, phase }));
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
      .map(t => typeof t === 'string' ? t : t.text);
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
    const q = (filterType.value||'').toLowerCase();
    const rows = list
      .filter(x=> !q || (x.type||'').toLowerCase().includes(q))
      .map(renderRow)
      .join('');
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

  function renderRow(d){
    const items = (d.todos || []).slice(0, 4);
    const preview = items.map(t => {
      const ph = _todoPhase(t);
      const icon = _phaseIcon[ph] || 'sync';
      const color = _phaseColor[ph] || 'var(--muted)';
      return '<span style="display:inline-flex;align-items:center;gap:2px;">'
        + '<span class="material-symbols-outlined" style="font-size:13px;color:' + color + ';">' + icon + '</span>'
        + escapeHtml(_todoText(t))
        + '</span>';
    }).join(' <span style="color:var(--muted);">&#8226;</span> ');
    const more = (d.todos||[]).length > 4 ? ' <span class="muted">(+' + ((d.todos.length - 4)) + ')</span>' : '';
    return '<tr data-id="' + d._id + '">'
      + '<td><input type="checkbox" class="row-check"></td>'
      + '<td><strong>' + escapeHtml(d.type||'') + '</strong></td>'
      + '<td>' + preview + more + '</td>'
      + '<td class="group-actions">'
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

  filterType.addEventListener('input', ()=> renderRows(current));

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

  refresh();
})();
