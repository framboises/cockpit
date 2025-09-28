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

  function formToPayload(){
    const fd = new FormData(form);
    // ✅ Corrigé: regexp sur une seule ligne
    const lines = (fd.get('todos')||'').split(/\r?\n/).map(s=>s.trim()).filter(Boolean);
    return {
      type: (fd.get('type')||'').trim(),
      todos: lines
    };
  }

  function payloadToForm(doc){
    form.reset();
    form.elements._id.value = doc._id || '';
    form.elements.type.value = doc.type || '';
    // ✅ Corrigé: string '\n' sur une seule ligne
    form.elements.todos.value = (doc.todos||[]).join('\n');
  }

  function renderRows(list){
    const q = (filterType.value||'').toLowerCase();
    const rows = list
      .filter(x=> !q || (x.type||'').toLowerCase().includes(q))
      .map(renderRow)
      .join('');
    tbody.innerHTML = rows || `<tr><td colspan="4" class="muted">Aucun résultat</td></tr>`;
  }

  function renderRow(d){
    const preview = (d.todos||[]).slice(0,3).map(escapeHtml).join(' • ');
    const more = (d.todos||[]).length>3 ? ` <span class="muted">(+${(d.todos.length-3)})</span>` : '';
    return `
      <tr data-id="${d._id}">
        <td><input type="checkbox" class="row-check"></td>
        <td><strong>${escapeHtml(d.type||'')}</strong></td>
        <td>${preview}${more}</td>
        <td>
          <button class="btn btn-xs" data-action="edit">Éditer</button>
          <button class="btn btn-xs btn-danger" data-action="delete">Supprimer</button>
        </td>
      </tr>`;
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

  btnSave.addEventListener('click', async ()=>{
    const payload = formToPayload();
    const id = form.elements._id.value;
    if(!payload.type){ alert('Le type est requis.'); return; }
    if(id){ await API.update(id, payload); }
    else {
      const exists = current.some(x=> (x.type||'').toLowerCase() === payload.type.toLowerCase());
      if(exists && !confirm('Ce type existe déjà. Continuer quand même ?')) return;
      await API.create(payload);
    }
    closeModal();
    await refresh();
  });

  tbody.addEventListener('click', async (e)=>{
    const btn = e.target.closest('button'); if(!btn) return;
    const tr = e.target.closest('tr'); const id = tr?.dataset?.id; const act = btn.dataset.action;
    if(act==='edit'){
      const doc = current.find(x=>x._id===id) || await API.get(id);
      modalTitle.textContent = 'Éditer la catégorie';
      payloadToForm(doc);
      openModal();
    }
    if(act==='delete'){
      if(confirm('Supprimer cette catégorie et ses tâches ?')){ await API.remove(id); await refresh(); }
    }
  });

  checkAll.addEventListener('change', ()=>{
    $$('.row-check', tbody).forEach(cb=> cb.checked = checkAll.checked);
  });

  btnDeleteSel.addEventListener('click', async ()=>{
    const ids = $$('.row-check:checked', tbody).map(cb=> cb.closest('tr').dataset.id);
    if(ids.length===0) return alert('Aucun élément sélectionné.');
    if(confirm(`Supprimer ${ids.length} catégorie(s) ?`)){
      await API.bulkRemove(ids);
      await refresh();
      checkAll.checked = false;
    }
  });

  refresh();
})();