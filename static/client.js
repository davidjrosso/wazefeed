'use strict';
const base = document.querySelector('meta[name="app-base"]').content;
const $ = id => document.getElementById(id);
const fmt = t => new Intl.DateTimeFormat('es-AR', {timeZone:'America/Argentina/Cordoba',dateStyle:'short',timeStyle:'short',hour12:false}).format(new Date(t*1000));
const title = s => `${s.street}: ${s.from} → ${s.to}`;
const normalize = t => t.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
let state=null, busy=false, selection=new Set(), editing=null, editRevision=null, points=[], confirmCallback=null;
let map=null, lines=null, draft=null, firstFit=true;
const live = () => state?.activeEvents || [];
const isClosed = id => live().some(e => e.segmentId === id);
const available = () => (state?.segments || []).filter(s => !s.archived);
const chosen = () => available().filter(s => selection.has(s.id));
const visibleRows = () => (state?.segments || []).filter(s => ($('show-archived').checked || !s.archived) && normalize(title(s)).includes(normalize($('filter').value)));
function message(text) {$('message').textContent=text;}
function node(tag, text, className) {const el=document.createElement(tag); if(text !== undefined)el.textContent=text; if(className)el.className=className; return el;}
function controls() {
  const disabled=busy || !state || editing !== null;
  $('activate').disabled=disabled || !chosen().some(s=>!isClosed(s.id));
  $('reopen').disabled=disabled || !chosen().some(s=>isClosed(s.id));
  $('add-segment').disabled=disabled;
  $('clear-selection').disabled=disabled || selection.size===0;
  $('select-visible').disabled=disabled;
  $('logout').hidden=!state;
  $('save-segment').disabled=busy || !state || editRevision===null || points.length<2;
  $('cancel-editor').disabled=busy;
  $('point-count').textContent=`${points.length} / 100 puntos`;
  $('selection-count').textContent=`${chosen().length} tramos seleccionados`;
  $('selection-summary').textContent=`${chosen().filter(s=>!isClosed(s.id)).length} disponibles · ${chosen().filter(s=>isClosed(s.id)).length} con cierre activo`;
}
function fitMap() {if(map && lines.getLayers().length)map.fitBounds(lines.getBounds(),{padding:[35,35],maxZoom:17});}
function initMap() {
  if(!window.L){message('No se pudo cargar el mapa. Podés consultar la tabla; recargá antes de editar recorridos.');return;}
  map=L.map('map').setView([-32.174,-64.1165],16);
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{
    maxZoom:19,referrerPolicy:'strict-origin-when-cross-origin',
    attribution:'&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
  }).on('tileerror',()=>{$('tile-warning').hidden=false;}).addTo(map);
  lines=L.featureGroup().addTo(map);draft=L.featureGroup().addTo(map);
  map.on('click',e=>{if(editing!==null)addPoint(e.latlng);});
}
function drawMap() {
  if(!map)return;
  lines.clearLayers();
  for(const s of available()) {
    const selected=selection.has(s.id), closed=isClosed(s.id);
    const line=L.polyline(s.points,{color:closed?'#b54127':selected?'#245ac5':'#26877b',weight:selected?9:6,opacity:.85});
    line.bindTooltip(node('span',`${title(s)} · ${s.direction==='ONE_DIRECTION'?'Un sentido':'Ambos sentidos'}${closed?' · Cierre activo':''}`));
    line.on('click',e=>{L.DomEvent.stopPropagation(e);if(editing!==null)addPoint(e.latlng);else toggle(s.id);});
    lines.addLayer(line);
    if(selected && editing===null) {
      [s.points[0],s.points[s.points.length-1]].forEach((p,i)=>lines.addLayer(L.circleMarker(p,{radius:5,color:'#245ac5',fillOpacity:1}).bindTooltip(node('span',i===0?'Inicio: '+s.from:'Final: '+s.to),{permanent:false})));
    }
  }
  if(firstFit && available().length){fitMap();firstFit=false;}
}
function toggle(id) {if(!state||busy||editing!==null)return;selection.has(id)?selection.delete(id):selection.add(id);$('template-reviewed').checked=false;render();}
function render() {
  const keep=new Set(available().map(s=>s.id));selection=new Set([...selection].filter(id=>keep.has(id)));
  $('status').textContent=live().length?`${live().length} ${live().length===1?'tramo cerrado':'tramos cerrados'}`:'Sin cierres activos';
  $('timing').textContent=live().length?`Próximo vencimiento: ${fmt(Math.min(...live().map(e=>e.end)))}`:'Los tramos guardados están listos para seleccionar.';
  $('login').hidden=true;
  $('segments').replaceChildren(...visibleRows().map(s=>{
    const tr=node('tr'); const pick=node('td');const checkbox=node('input');checkbox.type='checkbox';checkbox.checked=selection.has(s.id);checkbox.disabled=s.archived||busy||editing!==null;checkbox.setAttribute('aria-label','Seleccionar '+title(s));checkbox.onchange=()=>toggle(s.id);pick.append(checkbox);tr.append(pick);
    const name=node('td');name.append(node('strong',s.street),node('small',`${s.from} → ${s.to}`));tr.append(name,node('td',s.direction==='ONE_DIRECTION'?'Desde → hasta':'Ambos sentidos'));
    const event=live().find(e=>e.segmentId===s.id);const status=node('td');status.append(node('span',s.archived?'Archivado':event?'Cerrado':'Disponible',event?'badge danger':'badge'));if(event)status.append(node('small','Hasta '+fmt(event.end)));tr.append(status);
    const actions=node('td');actions.className='row-actions';
    if(!s.archived){const edit=node('button','Editar');edit.disabled=busy||!!event||editing!==null;edit.onclick=()=>openEditor(s);actions.append(edit);}
    const archive=node('button',s.archived?'Restaurar':'Quitar del mapa');archive.disabled=busy||!!event||editing!==null;
    archive.onclick=()=>{
      const payload={action:s.archived?'restore_segment':'archive_segment',segmentId:s.id,revision:state.revision};
      ask(s.archived?'Restaurar tramo':'Quitar tramo del mapa',[title(s)],s.archived?'Volverá a estar disponible para seleccionar. No se activa ningún cierre.':'Se archivará y conservará su historial. No se elimina ningún cierre publicado.',()=>mutate(payload));
    };
    actions.append(archive);tr.append(actions);return tr;
  }));
  $('empty-list').hidden=visibleRows().length>0;
  const labels={activate:'Cierre por 1 hora',reopen:'Reapertura',save_segment:'Configuración',archive_segment:'Archivado',restore_segment:'Restauración',migrate:'Actualización',save_template:'Plantilla guardada',activate_template:'Operativo activado',finish_run:'Operativo finalizado',extend_run:'Operativo extendido',schedule_enable:'Calendario habilitado',schedule_pause:'Calendario pausado',schedule_generate:'Programación',archive_template:'Plantilla archivada',import_segments:'Calles importadas'};
  $('history').replaceChildren(...(state.history.length?state.history:[{action:'empty'}]).map(e=>{const li=node('li',`${labels[e.action]||'Sin operaciones'}${e.detail?' · '+e.detail:''}`);if(e.at)li.append(node('time',fmt(e.at)+' · '+e.actor));return li;}));
  drawMap();controls();if(typeof renderOperations==='function')renderOperations();
}
async function refresh() {
  try {
    const r=await fetch(base+'/api/state',{cache:'no-store'});const j=await r.json();
    if(!r.ok)throw Object.assign(new Error(j.error||'No se pudo consultar el estado.'),{status:r.status});
    state=j;render();
  } catch(error) {
    state=null;controls();$('status').textContent=error.status===401?'Ingresá para operar':'Estado no disponible';
    $('timing').textContent='Los controles están deshabilitados hasta confirmar el estado.';$('login').hidden=false;message(error.message);
    $('segments').replaceChildren();$('history').replaceChildren();if(lines)lines.clearLayers();
    $('operations-panel').hidden=true;$('admin-workspace').hidden=true;$('admin-toggle').hidden=true;$('identity').textContent='Sin sesión';
  }
}
async function mutate(payload, onSuccess) {
  if(busy||!state)return;busy=true;controls();message('Guardando…');
  try {
    const r=await fetch(base+'/api/state',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':state.csrf},body:JSON.stringify(payload)});const j=await r.json();
    if(!r.ok){if(r.status===409 && editing!==null && j.error?.startsWith('El estado cambió.')){editRevision=null;$('editor-message').textContent='Otra operación cambió la versión. Cancelá y abrí de nuevo el tramo antes de guardar.';}throw new Error(j.error||'No se pudo confirmar la operación.');}
    state=j;if(onSuccess)onSuccess();render();
    message(['activate','reopen','activate_template','finish_run','extend_run','schedule_enable','schedule_pause','archive_template'].includes(payload.action)?'Feed actualizado. La aplicación en Waze depende de su integración y próxima lectura.':'Configuración guardada. No se activaron cierres.');
  } catch(error) {message(error.message);if(editing===null)await refresh();}
  finally {busy=false;controls();if(state)render();}
}
function ask(heading, entries, warning, callback) {
  if(busy)return;
  $('confirm-title').textContent=heading;$('confirm-warning').textContent=warning;
  $('confirm-map').replaceChildren();
  $('confirm-list').replaceChildren(...entries.map(v=>node('li',v)));confirmCallback=callback;$('confirm-dialog').showModal();
}
function prepareClosure(action) {
  const selected=chosen().filter(s=>action==='activate'?!isClosed(s.id):isClosed(s.id));
  if(!selected.length)return;
  const payload={action,segmentIds:selected.map(s=>s.id),revision:state.revision,description:$('reason').value};
  ask(action==='activate'?`Cerrar ${selected.length} tramo(s) por 1 hora`:`Reabrir ${selected.length} tramo(s)`,selected.map(s=>`${title(s)} · ${s.direction==='ONE_DIRECTION'?'Un sentido':'Ambos sentidos'}`),action==='activate'?'Esta operación publica cierres reales en el feed. Confirmá que las calles estén cortadas.':'Se publicará el fin anticipado de estos cierres, conservando sus identificadores.',()=>mutate(payload));
}
function openEditor(segment=null) {
  if(busy||!state||editing!==null)return;
  if(!map){message('El mapa debe estar disponible para editar recorridos.');return;}
  editing=segment?segment.id:'';editRevision=state.revision;points=segment?segment.points.map(p=>[...p]):[];
  $('segment-form').reset();for(const key of ['street','from','to'])$(key).value=segment?.[key]||'';
  $('direction').value=segment?.direction||'ONE_DIRECTION';$('waze-id').value=segment?.wazeSegmentId||'';
  $('editor-title').textContent=segment?'Editar tramo':'Agregar tramo';$('editor-message').textContent='';$('editor').hidden=false;
  $('map-hint').textContent='Modo edición: hacé clic sobre el mapa para agregar puntos. Arrastralos para ajustar el trazado.';
  map.doubleClickZoom.disable();render();drawDraft();if(points.length)map.fitBounds(points,{padding:[40,40],maxZoom:18});$('street').focus({preventScroll:true});
}
function closeEditor() {editing=null;editRevision=null;points=[];$('editor').hidden=true;draft?.clearLayers();map?.doubleClickZoom.enable();$('map-hint').textContent='Seleccioná tramos en el mapa o en la tabla.';if(state)render();}
function addPoint(latlng) {if(busy)return;if(points.length>=100){message('El máximo es de 100 puntos por tramo.');return;}points.push([Number(latlng.lat.toFixed(7)),Number(latlng.lng.toFixed(7))]);drawDraft();}
function drawDraft() {
  if(!draft)return;draft.clearLayers();$('geometry-confirmed').checked=false;
  if(points.length>1)draft.addLayer(L.polyline(points,{color:'#a24eb0',weight:6,dashArray:'8 5'}));
  points.forEach((p,i)=>{const marker=L.marker(p,{draggable:true,icon:L.divIcon({className:'point-marker',html:String(i+1),iconSize:[25,25],iconAnchor:[12,12]})});marker.on('dragend',()=>{const ll=marker.getLatLng();points[i]=[Number(ll.lat.toFixed(7)),Number(ll.lng.toFixed(7))];drawDraft();});draft.addLayer(marker);});
  $('coordinates').value=points.map(p=>p.join(', ')).join('\n');controls();
}
$('segment-form').onsubmit=e=>{e.preventDefault();if(!state||editRevision===null||!$('geometry-confirmed').checked)return;
  const segment={street:$('street').value,from:$('from').value,to:$('to').value,direction:$('direction').value,wazeSegmentId:$('waze-id').value,points};
  if(editing)segment.id=editing;
  mutate({action:'save_segment',revision:editRevision,segment},closeEditor);
};
$('add-segment').onclick=()=>openEditor();$('cancel-editor').onclick=()=>{closeEditor();refresh();};
$('undo-point').onclick=()=>{points.pop();drawDraft();};$('clear-points').onclick=()=>{points=[];drawDraft();};
$('reverse-points').onclick=()=>{points.reverse();const a=$('from').value;$('from').value=$('to').value;$('to').value=a;drawDraft();};
$('fit-map').onclick=fitMap;$('filter').oninput=()=>{if(state)render();};$('show-archived').onchange=()=>{if(state)render();};
$('select-visible').onclick=()=>{for(const s of visibleRows())if(!s.archived)selection.add(s.id);render();};
$('clear-selection').onclick=()=>{selection.clear();render();};$('activate').onclick=()=>prepareClosure('activate');$('reopen').onclick=()=>prepareClosure('reopen');
$('confirm-cancel').onclick=()=>{confirmCallback=null;$('confirm-dialog').close();};
$('confirm-dialog').oncancel=()=>{confirmCallback=null;};
$('confirm-submit').onclick=()=>{const run=confirmCallback;confirmCallback=null;$('confirm-dialog').close();if(run)run();};
$('logout').onclick=async()=>{if(!state||busy)return;busy=true;controls();try{const r=await fetch(base+'/api/logout',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':state.csrf},body:'{}'});if(!r.ok)throw Error('No se pudo cerrar sesión.');location.assign(base+'/login');}catch(e){message(e.message);}finally{busy=false;controls();}};
$('copy').onclick=async()=>{try{await navigator.clipboard.writeText(location.origin+base+'/feed.json');message('URL del feed copiada.');}catch{message('Copiá la URL del feed que se muestra abajo.');}};
$('feedurl').textContent=location.origin+base+'/feed.json';
initMap();controls();refresh();setInterval(()=>{if(!busy&&editing===null&&!$('confirm-dialog').open)refresh();},20000);
window.addEventListener('beforeunload',event=>{if(editing!==null){event.preventDefault();event.returnValue='';}});
