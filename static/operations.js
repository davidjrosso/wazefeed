'use strict';
let templateEditing=null, templateRevision=null, adminVisible=false;
function opButton(text, action) {const b=node('button',text);b.type='button';b.disabled=busy;b.onclick=action;return b;}
function schematic(segments) {
  const ns='http://www.w3.org/2000/svg';
  const svg=document.createElementNS(ns,'svg');svg.setAttribute('viewBox','0 0 600 300');svg.setAttribute('role','img');svg.setAttribute('aria-label','Esquema numerado de las calles del operativo, norte arriba');
  if(!segments.length)return svg;
  const points=segments.flatMap(s=>s.points),cos=Math.cos(points[0][0]*Math.PI/180);
  const xs=points.map(p=>p[1]*cos),ys=points.map(p=>p[0]);
  const xmin=Math.min(...xs),xmax=Math.max(...xs),ymin=Math.min(...ys),ymax=Math.max(...ys),scale=Math.min(520/Math.max(xmax-xmin,.00001),220/Math.max(ymax-ymin,.00001));
  const project=p=>[300+(p[1]*cos-(xmin+xmax)/2)*scale,150-(p[0]-(ymin+ymax)/2)*scale];
  const add=(tag,attrs,text)=>{const n=document.createElementNS(ns,tag);for(const [k,v] of Object.entries(attrs))n.setAttribute(k,v);if(text)n.textContent=text;svg.append(n);};
  segments.forEach((s,i)=>{const pts=s.points.map(project);add('polyline',{points:pts.map(p=>p.join(',')).join(' '),fill:'none',stroke:'#147d76','stroke-width':7,'stroke-linecap':'round'});const m=Math.floor((pts.length-1)/2),a=pts[m],b=pts[m+1],angle=Math.atan2(b[1]-a[1],b[0]-a[0])*180/Math.PI;for(const d of s.direction==='BOTH_DIRECTIONS'?[0,180]:[0])add('path',{d:'M -6 -5 L 1 0 L -6 5',fill:'none',stroke:'#fff','stroke-width':2,transform:`translate(${(a[0]+b[0])/2+(d?12:-12)},${(a[1]+b[1])/2}) rotate(${angle+d})`});add('circle',{cx:pts[0][0],cy:pts[0][1],r:12,fill:'#123e50'});add('text',{x:pts[0][0],y:pts[0][1]+4,'text-anchor':'middle',fill:'#fff','font-size':12},String(i+1));});
  add('text',{x:540,y:25,fill:'#526b75','font-size':14},'N ↑');return svg;
}
function opAsk(titleText, segments, warning, payload, done) {
  const revision=state.revision;
  ask(titleText,segments.map((s,i)=>`${i+1}. ${title(s)} · ${s.direction==='BOTH_DIRECTIONS'?'Ambos sentidos':'Desde → hasta'}`),warning,()=>mutate({...payload,revision},done));
  $('confirm-map').append(schematic(segments));
}
function renderOperations() {
  if(!state)return;
  const admin=state.role==='admin';
  $('identity').textContent=`${state.user} · ${admin?'Administración':'Operación'}`;
  $('operations-panel').hidden=false;$('admin-toggle').hidden=!admin;
  $('admin-workspace').hidden=!admin||!adminVisible;
  const templates=state.templates.filter(t=>!t.archived),now=state.serverTime;
  $('no-templates').hidden=templates.length>0;
  $('template-cards').replaceChildren(...templates.map(t=>{
    const card=node('article',undefined,'card template-card');card.append(node('span',t.reason,'badge'),node('h3',t.name),node('p',`${t.segments.length} tramos · ${t.durationMinutes} minutos`));
    if(t.source==='map') {
      card.append(node('p','Plantilla del catálogo OSM. Se gestiona y activa desde la consola nueva.','small'));
      const link=node('a','Abrir Plantillas en la consola nueva →');link.href=base+'/';card.append(link);return card;
    }
    card.append(node('p',t.enabled?'Calendario habilitado · '+t.schedule.time:'Activación manual','small'));
    const active=state.runs.some(r=>r.templateId===t.id&&r.start<=now&&now<r.end);
    const activate=opButton(active?'Operativo en curso':'Activar ahora',()=>opAsk('Activar '+t.name,t.segments,`Inicio AHORA · duración ${t.durationMinutes} minutos. Confirmá que estas calles están efectivamente cortadas. Se publicará el lote completo en el feed; Waze lo procesa después.`,{action:'activate_template',templateId:t.id}));activate.className='primary';activate.disabled=busy||active;card.append(activate);
    if(admin){const controls=node('div',undefined,'toolbar');
      const edit=opButton('Editar plantilla',()=>editTemplate(t));edit.disabled=busy||t.enabled;controls.append(edit);
      if(t.schedule)controls.append(opButton(t.enabled?'Pausar calendario':'Habilitar calendario',()=>opAsk(t.enabled?'Pausar '+t.name:'Habilitar '+t.name,t.segments,t.enabled?'Se cancelan las próximas ocurrencias, no las que ya están en curso.':'Esto PUBLICA cierres programados: '+scheduleText(t)+'. Se mantendrán publicados los próximos 7 días aunque cierres el navegador.',{action:t.enabled?'schedule_pause':'schedule_enable',templateId:t.id})));
      controls.append(opButton('Archivar',()=>opAsk('Archivar '+t.name,t.segments,'Se oculta la plantilla y se cancelan sus próximas ocurrencias. Los operativos en curso se finalizan por separado.',{action:'archive_template',templateId:t.id})));card.append(controls);
    }return card;
  }));
  const runs=[...state.runs].sort((a,b)=>a.start-b.start);
  $('run-cards').replaceChildren(...(runs.length?runs.map(r=>{const card=node('article',undefined,'run-card');const active=r.start<=now;card.append(node('h3',r.name),node('span',active?'En curso en el feed':'Programado en el feed',active?'badge danger':'badge'),node('p',`${fmt(r.start)} → ${fmt(r.end)} · ${r.segments.length} tramos`));const buttons=node('div',undefined,'toolbar');
    if(active)buttons.append(opButton('Extender 1 hora',()=>opAsk('Extender '+r.name,r.segments,'Nuevo final: '+fmt(r.end+3600)+'. Mantiene las calles y el inicio del operativo.',{action:'extend_run',runId:r.id,minutes:60})));
    buttons.append(opButton(active?'Finalizar operativo':'Cancelar esta fecha',()=>opAsk(active?'Finalizar '+r.name:'Cancelar '+r.name,r.segments,'Solo finaliza este operativo. Si otra plantilla mantiene una calle cerrada, su cierre continúa. Una fecha cancelada no vuelve a generarse automáticamente.',{action:'finish_run',runId:r.id})));card.append(buttons);return card;}):[node('p','No hay operativos activos ni programados.','small')]));
  $('template-selection').textContent=`${chosen().length} tramos para la plantilla: `+chosen().map(s=>s.street).join(', ');
  $('save-template').disabled=busy||!chosen().length;
}
function scheduleText(t){const names=['lunes','martes','miércoles','jueves','viernes','sábado','domingo'];return `${t.schedule.from} a ${t.schedule.until}; ${t.schedule.weekdays.map(d=>names[d]).join(', ')} a las ${t.schedule.time}; ${t.durationMinutes} min; excepciones: ${t.schedule.exceptions.join(', ')||'ninguna'}`;}
function resetTemplate(){templateEditing=null;templateRevision=null;$('template-form').reset();$('schedule-fields').hidden=true;$('template-heading').textContent='Preparar una plantilla compartida';}
function editTemplate(t){templateEditing=t.id;templateRevision=state.revision;adminVisible=true;selection=new Set(t.segments.map(s=>s.id));$('template-name').value=t.name;$('template-reason').value=t.reason;$('template-description').value=t.description;$('template-duration').value=t.durationMinutes;$('template-reviewed').checked=false;$('template-recurring').checked=!!t.schedule;$('schedule-fields').hidden=!t.schedule;if(t.schedule){$('schedule-from').value=t.schedule.from;$('schedule-until').value=t.schedule.until;$('schedule-time').value=t.schedule.time;$('schedule-exceptions').value=t.schedule.exceptions.join('\n');for(const input of $('weekdays').querySelectorAll('input'))input.checked=t.schedule.weekdays.includes(Number(input.value));}$('template-heading').textContent='Editar '+t.name;render();map?.invalidateSize();$('template-form').scrollIntoView({block:'start'});}
$('admin-toggle').onclick=()=>{adminVisible=!adminVisible;renderOperations();map?.invalidateSize();if(adminVisible)fitMap();};
$('template-recurring').onchange=()=>{$('schedule-fields').hidden=!$('template-recurring').checked;};
$('template-reason').onchange=()=>{$('template-description').value=({Inundación:'Cierre por inundación',Escuelas:'Cierre por ingreso escolar',Obras:'Cierre por obras',Evento:'Cierre por evento',Otro:'Cierre municipal'})[$('template-reason').value];};
$('cancel-template').onclick=resetTemplate;
$('template-form').onsubmit=e=>{e.preventDefault();if(!state||state.role!=='admin'||!$('template-reviewed').checked)return;
  const schedule=$('template-recurring').checked?{from:$('schedule-from').value,until:$('schedule-until').value,time:$('schedule-time').value,weekdays:[...$('weekdays').querySelectorAll('input:checked')].map(i=>Number(i.value)),exceptions:$('schedule-exceptions').value.split(/\s+/).filter(Boolean)}:null;
  const template={id:templateEditing,name:$('template-name').value,reason:$('template-reason').value,description:$('template-description').value,durationMinutes:Number($('template-duration').value),segmentIds:chosen().map(s=>s.id),schedule};
  const revision=templateRevision??state.revision;
  ask('Guardar plantilla: '+template.name,chosen().map(title),'Guarda una copia revisada de las calles. No modifica operativos activos ni habilita el calendario.',()=>mutate({action:'save_template',revision,template},resetTemplate));$('confirm-map').append(schematic(chosen()));
};
$('import-file').onchange=async e=>{const file=e.target.files[0];if(!file)return;try{if(file.size>1000000)throw Error('El archivo supera 1 MB.');const bundle=JSON.parse(await file.text());if(bundle.format!=='municipal-closures-segments'||!Array.isArray(bundle.segments)||!bundle.segments.length||bundle.segments.length>200)throw Error('Archivo incompatible.');if(!bundle.segments.every(s=>typeof s.street==='string'&&Array.isArray(s.points)&&s.points.length>=2&&s.points.length<=100&&s.points.every(p=>Array.isArray(p)&&p.length===2&&p.every(Number.isFinite))))throw Error('Geometrías inválidas.');opAsk('Importar calles desde Waze',bundle.segments,'Revisá el trazado y los sentidos. Incorpora calles al catálogo, sin cerrar ninguna.',{action:'import_segments',bundle});}catch(error){message(error.message);}finally{e.target.value='';}};
// Any changed selection or form data requires a fresh human review.
$('template-form').addEventListener('input',e=>{if(e.target.id!=='template-reviewed')$('template-reviewed').checked=false;});
