'use strict';
(() => {
  const $ = id => document.getElementById(id), base = document.body.dataset.base;
  const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text !== undefined) n.textContent = text; return n; };
  let state, catalog, map, currentView = 'create', pending, busy = false, actionHandler, publication;
  const selected = new Map(), layers = new Map(), targets = new Map();
  let oldLayers, selectionState;
  let templateContext = null, editingTemplate = false, agenda, users, monitor;
  const message = (text, error = false) => { $('notice').textContent = text; $('notice').classList.toggle('error', error); $('notice').hidden = !text; };
  const format = stamp => new Intl.DateTimeFormat('es-AR', {timeZone: state.city.timezone, day:'2-digit', month:'2-digit', hour:'2-digit', minute:'2-digit'}).format(new Date(stamp * 1000));
  const serverNow = () => state.serverTime + (Date.now() - state.receivedAt) / 1000;
  const timed = s => s.start <= serverNow() && s.end > serverNow() && s.closedAt === null;
  async function api(path, body) {
    const controller = new AbortController(), timeout = setTimeout(() => controller.abort(), 20000);
    try {
      const response = await fetch(base + path, {signal:controller.signal, credentials:'same-origin', headers:body ? {'Content-Type':'application/json', 'X-CSRF-Token':state.csrf} : {}, ...(body ? {method:'POST', body:JSON.stringify(body)} : {})});
      if (response.status === 401) {
        $('workspace').hidden = true; $('auth').hidden = false;
        document.querySelectorAll('dialog[open]').forEach(d => d.close());
        throw Object.assign(new Error('La sesión terminó. Volvé a ingresar con la misma cuenta para comprobar la operación.'), {status:401});
      }
      let data;
      try { data = await response.json(); }
      catch (_) { throw new Error('El servidor respondió sin datos válidos. No se pudo confirmar el resultado.'); }
      if (!response.ok) throw Object.assign(new Error(data.error || 'El servidor no pudo confirmar la operación.'), {status:response.status});
      return data;
    } catch (error) {
      if (error.name === 'AbortError') throw new Error('La respuesta demoró demasiado. La orden puede haberse guardado.');
      throw error;
    } finally { clearTimeout(timeout); }
  }
  function accept(data) { state = {...data, receivedAt:Date.now()}; renderState(); }
  async function refresh() { accept(await api('/api/state')); }
  async function mutate(body) {
    if(state.role==='viewer') throw new Error('Tu cuenta es de consulta: solo lectura.');
    if (busy) return false;
    busy = true;
    document.querySelectorAll('dialog button').forEach(b => b.disabled = true);
    try { accept(await api('/api/state', body)); return true; }
    finally { busy = false; document.querySelectorAll('dialog button').forEach(b => b.disabled = false); }
  }
  const request = (action, rest = {}) => ({action, revision:state.revision, requestId:crypto.randomUUID(), ...rest});
  function renderRecovery() {
    const waiting = !!publication?.pending;
    $('recovery').hidden = !waiting;
    if (waiting) $('recovery-detail').textContent = (publication.pending.name || publication.pending.reason)+'. Comprobá el resultado antes de preparar otro cierre. No se enviará nada automáticamente.';
    if (waiting) $('review').disabled = true;
  }
  function view(name, fitMonitor=true) {
    if(name==='users' && state.role!=='admin') return;
    if(name==='create' && state.role==='viewer') name='runs';
    currentView = name;
    for (const n of ['create','templates','agenda','runs','issues','users','monitor']) $(n+'-view').hidden = n !== name;
    document.querySelectorAll('[data-view]').forEach(b => b.classList.toggle('active', b.dataset.view === name));
    if(name==='users') users.load();
    if(name==='monitor') monitor.load(fitMonitor);
    if (map) map.invalidateSize();
  }
  function roadStyle(s) {
    const active = state.activeEvents.some(e => e.segmentId === s.id), chosen = selected.has(s.id);
    return {color: chosen ? '#009f90' : active ? '#d05a54' : s.warnings.length ? '#c29843' : '#487d8b', weight:chosen ? 9 : active ? 7 : map.getZoom() < 15 ? 2 : 5, opacity:chosen || active ? .95 : s.warnings.length ? .55 : .3};
  }
  function paint() {
    if (!map) return;
    for (const s of catalog.segments) layers.get(s.id).setStyle(roadStyle(s));
    for (const id of selected.keys()) layers.get(id).bringToFront();
    oldLayers.clearLayers();
    const known = new Set(catalog.segments.map(s => s.id));
    for (const e of state.activeEvents) if (!known.has(e.segmentId)) {
      const line = L.polyline(e.snapshot.points, {color:'#d05a54', weight:7}).addTo(oldLayers);
      line.bindTooltip(el('span', '', e.snapshot.street+' · cierre activo'));
      line.on('click', () => view('runs'));
    }
  }
  function detachTemplate() {
    if (templateContext && !editingTemplate) {
      templateContext = null;
      message('Selección personalizada: este cierre ya no modifica ni activa la plantilla original.');
    }
  }
  function choose(s) { if(state.role==='viewer'){view('runs');return;} detachTemplate(); if (selected.has(s.id)) selected.delete(s.id); else selected.set(s.id, {road:s, direction:s.oneway ? 'AB' : 'BOTH'}); view('create'); renderSelection(); paint(); }
  function button(text, fn, cls = '') { const b = el('button', cls, text); b.type = 'button'; b.addEventListener('click', fn); return b; }
  function report(s) {
    $('action-title').textContent = 'Informar un problema de mapa';
    const body = $('action-body'); body.replaceChildren(el('p','',s.street+' · '+s.from+' → '+s.to));
    const label = el('label','','¿Qué hay que corregir?'), input = el('textarea'); input.maxLength = 500; input.rows = 4; input.placeholder = 'Ej. Esta cuadra tiene un solo sentido…'; label.append(input); body.append(label,el('p','field-help','Se guarda un aviso para revisión local. No modifica OSM ni envía correos.'));
    $('action-confirm').textContent = 'Guardar aviso';
    let payload;
    actionHandler = async () => {
      if (!input.value.trim()) throw new Error('Describí el problema.');
      payload ||= request('report_issue', {segmentId:s.id, note:input.value.trim()});
      if (await mutate(payload)) { $('action-dialog').close(); message('Aviso guardado para revisión local.'); }
    };
    $('action-dialog').showModal();
  }
  function renderSelection() {
    const list = $('selected'); list.replaceChildren();
    $('selected-count').textContent = selected.size+' cuadra'+(selected.size===1?'':'s')+' seleccionada'+(selected.size===1?'':'s');
    if (!selected.size) {
      const empty = el('div','empty-state','⌖'); empty.append(el('strong','','Empezá por el mapa'),el('p','','Elegí las cuadras que necesitás cerrar.')); list.append(empty);
    }
    let index = 0;
    for (const [id, item] of selected) {
      const s = item.road, card = el('div','road-card'+(s.warnings.length?' warn':'')), head = el('div','road-head');
      const remove = button('×', () => choose(s), 'icon-button'); remove.setAttribute('aria-label','Quitar '+s.street+' de la selección');
      head.append(el('span','road-number',++index),el('strong','',s.street),remove); card.append(head, el('p','',s.from+' → '+s.to+' · '+s.length+' m'));
      if (s.oneway) card.append(el('p','','→ Sentido único · '+s.from+' hacia '+s.to));
      else {
        const select = el('select'); select.setAttribute('aria-label','Sentido afectado de '+s.street+' '+index);
        for (const [value,text] of [['BOTH','↔ Ambos sentidos'],['AB','→ '+s.from+' hacia '+s.to],['BA','← '+s.to+' hacia '+s.from]]) { const option = el('option','',text); option.value = value; select.append(option); }
        select.value = item.direction; select.addEventListener('change',() => { item.direction = select.value; detachTemplate(); renderTemplateContext(); }); card.append(select);
      }
      s.warnings.forEach(w => card.append(el('p','warning','⚠ '+w)));
      const events = state.pendingEvents.filter(e => e.segmentId === id);
      if (events.length) {
        const closures = el('div','road-closures');
        closures.append(el('strong','',events.length+' cierre(s) vigente(s) o programado(s)'));
        for (const event of events) {
          const run = state.runs.find(r => r.id === event.runId), row = el('div','road-closure');
          const direction = event.snapshot.direction === 'BOTH_DIRECTIONS' ? 'Ambos sentidos' : event.snapshot.from+' → '+event.snapshot.to;
          row.append(el('p','',(run?.name || 'Cierre anterior')+' · '+direction),el('p','',format(event.start)+' → '+format(event.end)));
          if (run) row.append(button(event.start > serverNow() ? 'Cancelar en este operativo' : 'Reabrir en este operativo', () => confirmAction('¿Retirar esta cuadra del operativo?',run.name+' · '+direction+'. Los demás cierres de esta calle se conservan.',request('reopen_roads',{runId:run.id,segmentIds:[id]}),'Confirmar en feed'),'danger'));
          closures.append(row);
        }
        card.append(closures);
      }
      card.append(button('Informar un error en esta calle',() => report(s),'text-button')); list.append(card);
    }
    $('review').disabled = !selected.size || [...selected.values()].some(s => s.road.warnings.length);
    $('save-template').disabled = $('review').disabled;
    renderTemplateContext();
    renderRecovery();
  }
  function renderTemplateContext() {
    const isAdmin = state.role === 'admin';
    $('save-template').hidden = !isAdmin || editingTemplate;
    $('new-template').hidden = !isAdmin;
    $('review').textContent = editingTemplate ? 'Revisar y guardar plantilla →' : 'Revisar cierre →';
    $('review-help').textContent = editingTemplate ? 'Guardar no publica ni cambia cierres.' : 'Se publica únicamente después de tu aprobación.';
    $('create-title').textContent = editingTemplate ? (templateContext ? 'Editá tu plantilla.' : 'Prepará tu plantilla.') : 'Seleccioná. Revisá. Publicá.';
    $('create-eyebrow').textContent = editingTemplate ? 'CALLES LISTAS PARA LA PRÓXIMA VEZ' : 'UN CIERRE EN TRES PASOS';
    $('closure-start').hidden = editingTemplate;
    $('end-mode').parentElement.hidden = editingTemplate;
    $('operation-name-label').textContent = editingTemplate ? 'Nombre de la plantilla' : 'Nombre del operativo';
    $('name-optional').hidden = editingTemplate;
    // A template stores a duration, never an absolute end date.
    if (editingTemplate) { $('end-mode').value='duration'; $('end-mode').onchange(); }
    $('end-mode').disabled = editingTemplate;
    $('template-context').hidden = !templateContext && !editingTemplate;
    $('template-context-title').textContent = templateContext ? templateContext.name+' · v'+templateContext.version : 'Nueva plantilla';
    let detail = editingTemplate ? 'Elegí las cuadras, sus sentidos, el motivo y la duración sugerida.' : 'Elegí el horario y revisá el cierre. La plantilla guardada no cambia.';
    if (templateContext && catalog) {
      const latest = state.templates.find(t=>t.id===templateContext.id);
      if (!latest || latest.archived || latest.version!==templateContext.version) detail='Esta plantilla cambió o fue archivada. Salí y volvé a elegirla antes de continuar.';
      else if (templateContext.catalogVersion!==catalog.version) detail='El catálogo cambió. Revisá todas las cuadras y los sentidos antes de guardar una nueva versión.';
    }
    $('template-context-detail').textContent = detail;
  }
  function durationValue() { return Number($('duration').value==='custom' ? $('custom-duration').value : $('duration').value); }
  function setDuration(value) {
    const option=[...$('duration').options].some(o=>o.value===String(value));
    $('duration').value=option ? String(value) : 'custom'; $('custom-duration').value=value; $('duration').onchange();
  }
  function selectionPayload() { return [...selected].map(([id,s])=>({id,direction:s.direction})); }
  function fitSelection() {
    if (selected.size) map.fitBounds(L.latLngBounds([...selected.values()].flatMap(s=>s.road.points)),{padding:[65,65],maxZoom:17});
  }
  function replaceDraft(fn) {
    if (publication?.pending) { message('Primero comprobá la publicación pendiente.',true); return; }
    if (!selected.size && !editingTemplate) { fn(); return; }
    $('action-title').textContent='¿Reemplazar la selección actual?';
    $('action-body').replaceChildren(el('p','','Se descartan los cambios no guardados del formulario. Los cierres y las plantillas guardadas no se modifican.'));
    $('action-confirm').textContent='Reemplazar selección';
    actionHandler=async()=>{ $('action-dialog').close(); fn(); };
    $('action-dialog').showModal();
  }
  function startTemplate(t, edit=false) {
    replaceDraft(()=>{
      // Re-read the version after the replacement confirmation.
      t=t ? state.templates.find(item=>item.id===t.id) : null;
      if (t && (t.archived || t.source!=='map')) { message('La plantilla ya no está disponible.',true); return; }
      if (t && !edit && t.catalogVersion!==catalog.version) { message('Administración debe revisar esta plantilla porque cambió el mapa.',true); return; }
      const rows=[]; let missing=0, directionChanges=0;
      for(const item of t?.selection || []) {
        const road=catalog.segments.find(s=>s.id===item.id);
        if (road) {
          let direction=item.direction;
          if(road.oneway && direction!=='AB') { direction='AB'; directionChanges++; }
          rows.push([item.id,{road,direction}]);
        } else missing++;
      }
      if (missing && !edit) { message('Hay calles que ya no existen en el catálogo. Pedí revisión a administración.',true); return; }
      templateContext=t ? structuredClone(t) : null; editingTemplate=edit; selected.clear();
      for (const [id,item] of rows) selected.set(id,item);
      $('reason').value=t?.reason || ''; $('reason').onchange();
      $('description').value=t?.description || ''; $('description-label').hidden=!t?.description && $('reason').value!=='Otro';
      $('operation-name').value=t?.name || '';
      $('start-mode').value='now'; $('start-mode').onchange(); $('end-mode').value='duration'; setDuration(t?.durationMinutes || 60); $('end-mode').onchange();
      pending=null; view('create'); renderSelection(); paint(); fitSelection();
      const warnings=[];
      if(missing) warnings.push(missing+' cuadra(s) ya no están en el catálogo y se quitaron del borrador. Seleccioná sus reemplazos antes de guardar.');
      if(directionChanges) warnings.push(directionChanges+' cuadra(s) ahora tienen sentido único. Se ajustó el borrador: revisá los sentidos antes de guardar.');
      message(warnings.length ? warnings.join(' ') : edit ? 'Modo plantilla: guardar no publica cierres.' : 'Plantilla cargada. Revisá el horario y aprobá el resumen para publicarla.',!!warnings.length);
    });
  }
  function renderTemplates() {
    const list=$('templates'); list.replaceChildren();
    const available=state.templates.filter(t=>!t.archived), query=$('template-search').value.trim().toLocaleLowerCase('es');
    $('template-count').textContent=available.filter(t=>t.source==='map').length;
    const filtered=available.filter(t=>(t.name+' '+t.reason).toLocaleLowerCase('es').includes(query)).sort((a,b)=>Number(b.source==='map')-Number(a.source==='map') || a.name.localeCompare(b.name,'es'));
    if (!filtered.length) {
      const empty=el('div','empty-state','▤'); empty.append(el('strong','',query?'Sin coincidencias':'Tus calles, listas para actuar'),el('p','',query?'Probá con otro nombre.':'Guardá grupos para lluvias, escuelas o eventos. No se activa ningún cierre al guardarlos.')); list.append(empty);
    }
    for(const t of filtered) {
      const card=el('article','template-card'), modern=t.source==='map', stale=modern&&catalog&&t.catalogVersion!==catalog.version;
      card.append(el('span','badge template-badge',modern?'PLANTILLA · V'+t.version:'ADMINISTRACIÓN ANTERIOR'),el('h3','',t.name),el('p','',t.reason+' · '+t.segments.length+' cuadra(s) · '+t.durationMinutes+' min sugeridos'));
      const streets=[...new Set(t.segments.map(s=>s.street))]; card.append(el('p','template-streets',streets.slice(0,4).join(' · ')+(streets.length>4?' y '+(streets.length-4)+' más':'')));
      const active=state.runs.filter(r=>r.templateId===t.id).length;
      if(active) card.append(el('p','field-help',active+' operativo(s) activo(s) o próximo(s). Una nueva publicación será independiente.'));
      if(stale) card.append(el('p','warning','El mapa cambió. Administración debe revisar y guardar esta plantilla nuevamente.'));
      const actions=el('div','run-actions');
      if(modern) {
        const use=button('Preparar cierre →',()=>startTemplate(t),'primary'); use.disabled=!!stale; if(state.role!=='viewer') actions.append(use);
        if(t.schedule || state.role==='admin') actions.append(button('Calendario',()=>{view('agenda');if(t.schedule)agenda.open(t);else agenda.configure(t);}));
        if(state.role==='admin') {
          const edit=button('Editar',()=>startTemplate(t,true));edit.disabled=t.enabled;actions.append(edit);
          const archive=button('Archivar',()=>{
            $('action-title').textContent='¿Archivar «'+t.name+'»?';
            $('action-body').replaceChildren(el('p','','Se quitará de la biblioteca. Los operativos activos o programados no se cancelan.'));
            $('action-confirm').textContent='Archivar plantilla';
            const payload=request('archive_map_template',{templateId:t.id,templateVersion:t.version});
            actionHandler=async()=>{ if(await mutate(payload)){ $('action-dialog').close(); message('Plantilla archivada. Los operativos existentes se conservan.'); } };
            $('action-dialog').showModal();
          },'danger');archive.disabled=t.enabled;actions.append(archive);
          if(t.enabled) card.append(el('p','field-help','Pausá la agenda desde Calendario antes de editar o archivar.'));
        }
      } else {
        card.append(el('p','field-help','Conservada sin cambios. Se gestiona en la administración anterior; no se convirtió automáticamente al catálogo OSM.'));
        const link=el('a','','Abrir administración anterior ↗'); link.href=base+'/legacy'; actions.append(link);
      }
      card.append(actions); list.append(card);
    }
  }
  function saveTemplateDialog() {
    try {
      if(state.role!=='admin') throw new Error('Solo administración puede guardar plantillas.');
      if(publication?.pending) throw new Error('Primero comprobá la publicación pendiente.');
      if(!selected.size || [...selected.values()].some(s=>s.road.warnings.length)) throw new Error('Seleccioná calles sin advertencias pendientes.');
      if(!$('reason').value) throw new Error('Elegí un motivo.');
      if($('reason').value==='Otro' && !$('description').value.trim()) throw new Error('Completá el detalle del motivo.');
      const snapshot={catalogVersion:catalog.version,selection:selectionPayload(),reason:$('reason').value,description:$('description').value.trim()||$('reason').value,
        ...(editingTemplate && templateContext ? {templateId:templateContext.id,templateVersion:templateContext.version} : {})};
      const revision=state.revision;
      $('template-name').value=$('operation-name').value.trim(); $('template-duration').value=durationValue();
      $('template-name').readOnly=false; $('template-duration').readOnly=false; $('template-error').textContent='';
      $('template-dialog-title').textContent=snapshot.templateId?'Guardar nueva versión':'Guardar plantilla';
      $('template-confirm').textContent='Guardar plantilla';
      const summary=$('template-save-summary'); summary.replaceChildren(el('p','',snapshot.reason+' · '+selected.size+' cuadra(s)'));
      const list=el('ul','summary-list');
      for(const {road,direction} of selected.values()) list.append(el('li','',road.street+' · '+(direction==='BOTH'?'Ambos sentidos · ': '')+(direction==='BA'?road.to+' → '+road.from:road.from+' → '+road.to)));
      summary.append(list);
      let payload;
      $('template-form').onsubmit=async e=>{
        e.preventDefault(); if(busy)return;
        try {
          payload ||= request('save_map_template',{...snapshot,revision,name:$('template-name').value.trim(),durationMinutes:Number($('template-duration').value)});
          $('template-name').readOnly=true; $('template-duration').readOnly=true;
          if(await mutate(payload)) {
            $('template-dialog').close(); templateContext=null; editingTemplate=false; selected.clear();
            renderSelection(); paint(); view('templates'); message('Plantilla guardada. No se publicó ni se modificó ningún cierre.');
          }
        } catch(error) {
          $('template-error').textContent=error.status===409 ? error.message+' Cerrá este resumen y revisá la plantilla antes de guardar de nuevo.' : error.message+' Podés reintentar esta misma solicitud; no se duplica la plantilla.';
          $('template-confirm').textContent='Reintentar guardado'; await refresh().catch(()=>{});
        }
      };
      $('template-dialog').showModal();
    } catch(error) { message(error.message,true); }
  }
  function confirmAction(title, text, body, label) {
    $('action-title').textContent = title; $('action-body').replaceChildren(el('p','',text)); $('action-confirm').textContent = label;
    actionHandler = async () => { if (await mutate(body)) { $('action-dialog').close(); message('Feed actualizado. Los cierres de otros operativos se conservan.'); } };
    $('action-dialog').showModal();
  }
  function renderState() {
    $('operator').textContent = state.user+(state.role==='viewer'?' · Consulta':'');
    document.body.classList.toggle('is-viewer',state.role==='viewer');
    $('runs-view').querySelector('.panel-heading p').textContent=state.role==='viewer'?'Consultá los cierres activos y programados. Tu cuenta no puede modificarlos.':'Reabrí calles o finalizá un operativo. Los cierres superpuestos se conservan.';
    document.querySelector('.map-heading p').textContent=state.role==='viewer'?'Vista de consulta.':'Un clic, una cuadra.';
    document.querySelector('.overview-metric[data-view="templates"] small').textContent=state.role==='viewer'?'Ver biblioteca':'Preparar un cierre';
    $('users-nav').hidden=state.role!=='admin';
    if(state.role==='viewer'&&currentView==='create')view('runs');
    $('city-name').textContent = state.city.name; $('map-title').textContent = state.city.name;
    $('timezone').textContent = 'Horarios de '+state.city.name+' · '+state.city.timezone;
    $('legacy').hidden = state.role !== 'admin';
    renderTemplates(); renderTemplateContext(); agenda.render();
    $('sync').textContent = 'Actualizado '+format(state.serverTime);
    $('run-count').textContent = state.runs.length;
    $('active-count').textContent = new Set(state.activeEvents.map(e=>e.segmentId)).size;
    $('future-count').textContent = state.runs.filter(r=>r.start>serverNow()).length;
    $('overview-active').textContent = $('active-count').textContent;
    $('overview-next').textContent = $('future-count').textContent;
    $('overview-templates').textContent = state.templates.filter(t=>!t.archived&&t.source==='map').length;
    $('overview-date').textContent = new Intl.DateTimeFormat('es-AR',{timeZone:state.city.timezone,weekday:'long',day:'numeric',month:'long'}).format(new Date(state.serverTime*1000));
    $('overview-status').textContent='Estado sincronizado';document.querySelector('.command-overview').classList.remove('offline');
    const nextRun=[...state.runs].filter(r=>r.start>serverNow()).sort((a,b)=>a.start-b.start)[0];
    $('overview-summary').textContent=nextRun ? 'Próximo: '+nextRun.name+' · '+format(nextRun.start) : state.activeEvents.length ? 'Cierres publicados en el feed. Tu equipo tiene el control de cada operativo.' : state.role==='viewer'?'La información de tu ciudad, en un solo lugar. Acceso de solo lectura.':'Todo listo para actuar. Seleccioná calles o elegí una plantilla.';
    const runs = $('runs'), expanded = new Set([...runs.querySelectorAll('details[open]')].map(d=>d.dataset.run)); runs.replaceChildren();
    if (!state.runs.length) runs.append(el('div','empty-state','Sin operativos activos o próximos.'));
    for (const r of [...state.runs].sort((a,b)=>a.start-b.start)) {
      const events = state.pendingEvents.filter(e=>e.runId===r.id), card = el('article','run-card');
      card.append(el('span','badge'+(r.start>serverNow()?' future':''),r.start>serverNow()?'PROGRAMADO EN FEED':'ACTIVO EN FEED'),el('h3','',r.name),el('p','',r.reason+' · '+events.length+' cuadra(s)'),el('p','',format(r.start)+' → '+format(r.end)));
      const actions = el('div','run-actions');
      actions.append(button('Ver en mapa',()=>{ if(events.length) map.fitBounds(L.latLngBounds(events.flatMap(e=>e.snapshot.points)),{padding:[65,65],maxZoom:17}); }));
      if(state.role!=='viewer')actions.append(button(r.start>serverNow()?'Cancelar operativo':'Finalizar operativo',()=>confirmAction('¿Finalizar este operativo?','Se actualizará el feed. Si hay otro cierre sobre la misma calle, seguirá vigente hasta que termine.',{action:'finish_run', revision:state.revision, runId:r.id},'Finalizar en feed'),'danger'));
      card.append(actions);
      if(r.source==='map-schedule') card.append(el('p','field-help','Generado por agenda · '+r.scheduleDate+'. Finalizarlo excluye esa fecha para que no vuelva a generarse.'));
      const details = el('details'), sum = el('summary','','Ver calles y reabrir individualmente'); details.dataset.run=r.id; details.open=expanded.has(r.id); sum.className = 'field-help'; details.append(sum);
      for (const e of events) { const row = el('div','run-road'); row.append(el('span','',e.snapshot.street+' · '+e.snapshot.from+' → '+e.snapshot.to),button('Reabrir',()=>confirmAction('¿Reabrir esta cuadra?',e.snapshot.street+'. Solo se retira de este operativo; cualquier otro cierre superpuesto se conserva.',request('reopen_roads',{runId:r.id,segmentIds:[e.segmentId]}),'Reabrir en feed'))); details.append(row); }
      card.append(details); runs.append(card);
    }
    const legacyEvents = state.pendingEvents.filter(e=>!e.runId);
    if (legacyEvents.length) { const box=el('div','info-box',legacyEvents.length+' cierre(s) anterior(es) conservados. Se gestionan desde Administración anterior.'); runs.append(box); }
    const issues = $('issues'); issues.replaceChildren(); const open = (state.issues||[]).filter(i=>i.status==='open'); $('issue-count').textContent = open.length;
    if (!open.length) issues.append(el('div','empty-state','No hay avisos pendientes.'));
    for (const i of [...open].reverse()) {
      const card=el('article','issue-card'); card.append(el('h3','',i.street),el('p','',i.note),el('p','field-help',format(i.at)+' · '+i.actor));
      const link=el('a','','Ver calle en OpenStreetMap ↗'); link.href='https://www.openstreetmap.org/way/'+i.osmWayId; link.target='_blank'; link.rel='noopener'; card.append(link);
      if(state.role==='admin') card.append(button('Marcar revisado',()=>confirmAction('¿Marcar aviso como revisado?','Esto no modifica la cartografía. Tras corregir OSM, el administrador debe actualizar el catálogo del sistema.',request('resolve_issue',{issueId:i.id}),'Marcar revisado'),'text-button'));
      issues.append(card);
    }
    paint();
    const nextSelectionState = JSON.stringify([state.revision, state.pendingEvents.map(e => [e.id,e.start,e.end,e.closedAt])]);
    // Don't replace a direction selector under the operator's cursor every poll.
    if (catalog && nextSelectionState !== selectionState) { selectionState=nextSelectionState; renderSelection(); }
  }
  function initMap() {
    map=L.map('map',{zoomControl:false,preferCanvas:false}).setView(catalog.city.center,16);
    L.control.zoom({position:'bottomright'}).addTo(map);
    L.tileLayer(catalog.city.tileUrl,{maxZoom:19,attribution:'© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>'}).addTo(map);
    const [s,w,n,e]=catalog.city.bounds;
    L.rectangle([[s,w],[n,e]],{color:'#608d88',weight:1,dashArray:'7 7',fill:false,interactive:false}).addTo(map);
    map.setMaxBounds([[s-.015,w-.015],[n+.015,e+.015]]);
    oldLayers=L.layerGroup().addTo(map);
    for (const road of catalog.segments) {
      const line=L.polyline(road.points,{...roadStyle(road),interactive:false}).addTo(map), tip=el('div'); tip.append(el('strong','',road.street),el('div','',road.from+' → '+road.to));
      const target=L.polyline(road.points,{weight:18,opacity:0}).addTo(map);
      target.bindTooltip(tip,{sticky:true}); target.on('click',()=>choose(road)); layers.set(road.id,line); targets.set(road.id,target);
      // Wider transparent hit target; keyboard users can select via street search.
      const path=target.getElement(); if(path) { path.setAttribute('role','button'); path.setAttribute('tabindex','-1'); path.setAttribute('aria-label','Seleccionar '+road.street+': '+road.from+' a '+road.to); }
    }
    map.on('zoomend',paint); $('map-home').onclick=()=>map.setView(catalog.city.center,16);
    $('catalog-info').textContent=catalog.segments.length+' tramos · OSM '+(catalog.sourceTimestamp||'fecha no disponible').slice(0,10)+' · Área urbana configurada';
    if(!catalog.segments.length) message('No hay catálogo de calles cargado. No se pueden publicar cierres desde el mapa.',true);
    monitor.load();setInterval(()=>{if(!$('workspace').hidden)monitor.load();},60000);
    paint();
  }
  function cityTimestamp(value) {
    if(!value) throw new Error('Completá fecha y hora.');
    const [date, time]=value.split('T'), parts=(date+'-'+time.replace(':','-')).split('-').map(Number);
    if(parts.length!==5 || parts.some(n=>!Number.isFinite(n))) throw new Error('Fecha inválida.');
    const naive=Date.UTC(parts[0],parts[1]-1,parts[2],parts[3],parts[4]); let stamp=naive;
    const fmt=new Intl.DateTimeFormat('en-CA',{timeZone:state.city.timezone,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'});
    for(let i=0;i<3;i++){ const p=Object.fromEntries(fmt.formatToParts(stamp).map(x=>[x.type,x.value])); const local=Date.UTC(+p.year,+p.month-1,+p.day,+p.hour,+p.minute,+p.second); stamp+=naive-local; }
    const p=Object.fromEntries(fmt.formatToParts(stamp).map(x=>[x.type,x.value]));
    if(`${p.year}-${p.month}-${p.day}T${p.hour}:${p.minute}`!==value) throw new Error('La hora no existe en la zona horaria de la ciudad.');
    return stamp/1000;
  }
  function schematic(entries=[...selected.values()], root=$('summary-map')) {
    root.replaceChildren();
    const ns='http://www.w3.org/2000/svg', svg=document.createElementNS(ns,'svg'); svg.setAttribute('viewBox','0 0 560 210'); svg.setAttribute('role','img'); svg.setAttribute('aria-label','Esquema de las cuadras seleccionadas, con norte arriba. Los números corresponden al listado.');
    const points=entries.flatMap(x=>x.road.points), cos=Math.cos(points[0][0]*Math.PI/180), xs=points.map(p=>p[1]*cos), ys=points.map(p=>-p[0]);
    const xmin=Math.min(...xs), ymin=Math.min(...ys), dx=Math.max(...xs)-xmin, dy=Math.max(...ys)-ymin, scale=Math.min(470/(dx||.0001),135/(dy||.0001));
    const project=p=>[(p[1]*cos-xmin)*scale+45+(470-dx*scale)/2,(-p[0]-ymin)*scale+35+(135-dy*scale)/2];
    const add=(name,attrs,text)=>{const n=document.createElementNS(ns,name);for(const [k,v] of Object.entries(attrs))n.setAttribute(k,v);if(text)n.textContent=text;svg.append(n);return n;};
    add('text',{x:20,y:22,class:'north'},'N ↑   ESQUEMA · NO ES UN MAPA DE WAZE');
    let i=0;
    for(const {road,direction} of entries) {
      const pts=road.points.map(project); add('polyline',{points:pts.map(p=>p.join(',')).join(' '),class:'line'});
      const mid=pts[Math.floor((pts.length-1)/2)], last=pts[pts.length-1];
      const anchor=[(mid[0]+last[0])/2,(mid[1]+last[1])/2];
      add('circle',{cx:anchor[0],cy:anchor[1],r:11,class:'number'});add('text',{x:anchor[0],y:anchor[1]+4,'text-anchor':'middle'},String(++i));
      // A/B are the visible endpoint orientation used in the textual review.
      const a=direction==='BA'?pts[pts.length-1]:pts[0], b=direction==='BA'?pts[0]:pts[pts.length-1], angle=Math.atan2(b[1]-a[1],b[0]-a[0]);
      for(const [p,angle2] of direction==='BOTH'?[[b,angle],[a,angle+Math.PI]]:[[b,angle]]) add('polyline',{points:`${p[0]-8*Math.cos(angle2-.5)},${p[1]-8*Math.sin(angle2-.5)} ${p[0]},${p[1]} ${p[0]-8*Math.cos(angle2+.5)},${p[1]-8*Math.sin(angle2+.5)}`,class:'line'});
    }
    root.append(svg);
  }
  function prepare(event) {
    event.preventDefault(); if(!selected.size) return;
    if(editingTemplate) { saveTemplateDialog(); return; }
    try {
      if (publication?.pending) throw new Error('Primero comprobá la publicación pendiente desde el aviso superior.');
      if([...selected.values()].some(s=>s.road.warnings.length)) throw new Error('Quitá los tramos que requieren revisión local antes de publicar.');
      const reason=$('reason').value; if(!reason) throw new Error('Elegí un motivo.');
      const start=$('start-mode').value==='now'?'NOW':cityTimestamp($('start-at').value), duration=Number($('duration').value==='custom'?$('custom-duration').value:$('duration').value), endMode=$('end-mode').value;
      const end=endMode==='at'?cityTimestamp($('end-at').value):(start==='NOW'?serverNow():start)+duration*60;
      if(start!=='NOW' && start<serverNow()) throw new Error('La fecha de inicio ya pasó. Elegí AHORA o un horario futuro.');
      if(end-(start==='NOW'?serverNow():start)<60 || end-(start==='NOW'?serverNow():start)>7*86400) throw new Error('Elegí una duración de entre 1 minuto y 7 días.');
      if(reason==='Otro' && !$('description').value.trim()) throw new Error('Completá el detalle del motivo.');
      pending=request('publish_closure',{catalogVersion:catalog.version,selection:[...selected].map(([id,s])=>({id,direction:s.direction})),reason,name:$('operation-name').value.trim()||reason,description:$('description').value.trim()||reason,start,endMode,end,durationMinutes:duration});
      if(templateContext) Object.assign(pending,{templateId:templateContext.id,templateVersion:templateContext.version});
      const summary=$('summary'); summary.replaceChildren();
      for(const [title,value] of [['Operativo',pending.name],['Motivo',pending.description],['Inicio',start==='NOW'?'AHORA · al aprobar':format(start)],['Fin',endMode==='at'?format(end):`Después de ${duration} minutos`],['Publicación',selected.size+' cuadras en el feed de '+state.city.name]]) {const row=el('div','summary-row');row.append(el('span','',title),el('strong','',value));summary.append(row);}
      const list=el('ol','summary-list');for(const {road:s,direction:d} of selected.values()){const row=el('li','',s.street);row.append(el('small','',(d==='BOTH'?'↔ Ambos sentidos · ':d==='AB'?'→ ':'← ')+(d==='BA'?s.to+' → '+s.from:s.from+' → '+s.to)));list.append(row);}summary.append(list);
      const overlaps=state.pendingEvents.filter(e=>selected.has(e.segmentId)&&e.start<end&&e.end>(start==='NOW'?serverNow():start));
      if(overlaps.length) summary.append(el('div','info-box','Hay '+overlaps.length+' cierre(s) superpuesto(s). La calle seguirá cerrada mientras quede alguno vigente.'));
      schematic();$('publish-error').textContent='';$('publish').textContent='Aprobar y publicar en feed →';$('review-dialog').showModal();
    } catch(error) { message(error.message,true); }
  }
  $('publish').onclick=async()=>{
    if(!pending||busy)return; $('publish-error').textContent=''; $('publish').textContent='Publicando…';
    try{
      publication.stage(pending); renderRecovery();
      if(await mutate(publication.pending)){publication.clear();pending=null;templateContext=null;editingTemplate=false;selected.clear();renderSelection();paint();$('review-dialog').close();view('runs');message('Publicado en el feed. Podés abrir «Ver feed» para comprobar los datos. La aplicación en Waze no está confirmada.');}
    }
    catch(error){
      if (error.status === 409) {
        // The backend rejects these atomically; an already committed request
        // would have returned its receipt before revision/catalog validation.
        publication.clear(); pending=null; $('review-dialog').close();
        message('No se publicó este cierre: '+error.message+' Volvé a revisar antes de aprobar.',true);
      } else if (!publication?.pending) $('publish-error').textContent='No se envió la orden: el navegador no pudo guardar la solicitud para recuperarla. '+error.message;
      else $('publish-error').textContent=error.message+' Usá «Comprobar publicación» o reintentá esta misma orden. No prepares otra.';
      await refresh().catch(()=>{}); renderSelection();
    }
    finally{$('publish').textContent='Aprobar y publicar en feed →';}
  };
  $('recover').onclick=async()=>{
    if(busy || !publication?.pending) return;
    $('recover').disabled=true;
    try {
      const payload=publication.pending, result=await api('/api/requests/'+encodeURIComponent(payload.requestId));
      if(result.completed){
        publication.clear(); pending=null; templateContext=null; editingTemplate=false; selected.clear(); $('review-dialog').close();
        await refresh(); renderSelection(); view('runs');
        message('Publicación confirmada en el feed. No se creó ningún cierre adicional. Si ya venció, no aparecerá entre los activos.');
      } else {
        pending=payload;
        const summary=$('summary'); summary.replaceChildren(el('div','info-box','Todavía no hay confirmación de esta solicitud en el servidor. Podés reenviar exactamente la misma orden: conserva su identificador y no duplica un cierre ya guardado.'));
        $('summary-map').replaceChildren();
        summary.append(el('h3','',payload.name),el('p','',payload.description || payload.reason),el('p','',payload.start==='NOW'?'Inicio: AHORA cuando el servidor acepte la orden.':'Inicio: '+format(payload.start)),el('p','',payload.endMode==='at'?'Fin: '+format(payload.end):'Duración: '+payload.durationMinutes+' minutos'));
        const list=el('ul','summary-list');
        for(const item of payload.selection){const road=catalog.segments.find(s=>s.id===item.id);list.append(el('li','',road ? road.street+' · '+(item.direction==='BOTH'?'Ambos sentidos':item.direction==='BA'?road.to+' → '+road.from:road.from+' → '+road.to) : 'Tramo '+item.id+' (catálogo anterior)'));}
        summary.append(list); $('publish-error').textContent=''; $('publish').textContent='Reintentar la misma publicación →';
        if(!$('review-dialog').open) $('review-dialog').showModal();
      }
    } catch(error){message('No se pudo comprobar la publicación: '+error.message,true);}
    finally{$('recover').disabled=false;renderRecovery();}
  };
  $('action-confirm').onclick=async()=>{try{await actionHandler();}catch(error){let p=$('action-body').querySelector('.error');if(!p){p=el('p','error');$('action-body').append(p);}p.textContent=error.message;await refresh().catch(()=>{});}};
  document.querySelectorAll('[data-close]').forEach(b=>b.onclick=()=>$(b.dataset.close).close());
  document.querySelectorAll('dialog').forEach(d=>d.addEventListener('cancel',e=>{if(busy)e.preventDefault();}));
  users=ViaUsers({$,el,button,api,getState:()=>state,message,format});
  monitor=ViaMonitor({$,el,button,api,getState:()=>state,getMap:()=>map,view,message,format,prepare:a=>{
    replaceDraft(()=>{templateContext=null;editingTemplate=false;selected.clear();$('operation-name').value='';$('reason').value='';$('reason').onchange();view('create');renderSelection();paint();map.setView(a.point,18);message('Zona del reporte de Waze. Verificá la situación y seleccioná manualmente las cuadras y sentidos. Todavía no se publicó ningún cierre.');});
  }});
  document.querySelectorAll('[data-view]').forEach(b=>b.onclick=()=>{
    if(b.dataset.view==='create' && (editingTemplate || templateContext)) {
      replaceDraft(()=>{templateContext=null;editingTemplate=false;selected.clear();$('operation-name').value='';view('create');renderSelection();paint();});
    } else view(b.dataset.view);
  });
  $('clear').onclick=()=>{detachTemplate();selected.clear();renderSelection();paint();};
  $('new-template').onclick=()=>startTemplate(null,true);
  $('save-template').onclick=saveTemplateDialog;
  $('template-search').oninput=renderTemplates;
  $('leave-template').onclick=()=>replaceDraft(()=>{templateContext=null;editingTemplate=false;selected.clear();$('operation-name').value='';renderSelection();paint();message('Saliste de la plantilla. No se modificaron los datos guardados.');});
  $('start-mode').onchange=()=>{$('start-label').hidden=$('start-mode').value==='now';};
  $('end-mode').onchange=()=>{$('end-label').hidden=$('end-mode').value!=='at';$('duration-label').hidden=$('end-mode').value==='at';$('custom-label').hidden=$('end-mode').value==='at'||$('duration').value!=='custom';};
  $('duration').onchange=()=>{$('custom-label').hidden=$('duration').value!=='custom';};
  $('reason').onchange=()=>{$('description-label').hidden=$('reason').value!=='Otro';if($('reason').value!=='Otro')$('description').value='';};
  $('closure-form').onsubmit=prepare;
  const normalize=s=>s.normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase();
  $('search').addEventListener('input',()=>{
    const query=normalize($('search').value.trim()), results=$('search-results');results.replaceChildren();results.hidden=query.length<2;if(results.hidden||!catalog)return;
    const roads=catalog.segments.filter(s=>normalize(s.street+' '+s.from+' '+s.to).includes(query)).slice(0,30);
    for(const s of roads)results.append(button(s.street+' · '+s.from+' → '+s.to,()=>{map.fitBounds(L.latLngBounds(s.points),{padding:[80,80],maxZoom:18});targets.get(s.id).openTooltip();if(!selected.has(s.id))choose(s);results.hidden=true;$('search').value='';}));
    if(!roads.length)results.append(el('p','field-help','No hay coincidencias en el catálogo de esta ciudad.'));
  });
  $('logout').onclick=async()=>{try{await api('/api/logout',{});location.href=base+'/login';}catch(e){message(e.message,true);}};
  agenda=ViaAgenda({$,el,button,api,request,mutate,refresh,getState:()=>state,getCatalog:()=>catalog,message,format,schematic,view});
  (async()=>{try{accept(await api('/api/state'));catalog=await api('/api/catalog');publication=new ViaPublication(sessionStorage,[base,state.city.id,state.user]);$('auth').hidden=true;$('workspace').hidden=false;initMap();renderSelection();renderTemplates();agenda.render();setInterval(()=>{if(!busy)refresh().catch(e=>{$('sync').textContent='Sin conexión';$('overview-status').textContent='Sin conexión · datos anteriores';document.querySelector('.command-overview').classList.add('offline');message('No se pudo actualizar el estado: '+e.message,true);});},15000);}catch(e){message(e.message,true);}})();
})();
