'use strict';
// All dates/timestamps used for publication come from the server preview.
globalThis.ViaAgenda = ({$,el,button,api,request,mutate,refresh,getState,getCatalog,message,format,schematic,view}) => {
  const days=['Lunes','Martes','Miércoles','Jueves','Viernes','Sábado','Domingo'];
  const now=()=>getState().serverTime;
  const dayString=stamp=>new Intl.DateTimeFormat('en-CA',{timeZone:getState().city.timezone,year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date(stamp*1000));
  const brief=s=>s.weekdays.map(d=>days[d].slice(0,3)).join(' · ')+' / '+s.time+' → '+s.endTime+(s.endTime<s.time?' (+1 día)':'');
  let renderKey;
  function fail(error) { message(error.message,true); }
  function confirm(title, content, payload, label, done) {
    $('agenda-review-title').textContent=title; $('agenda-review-body').replaceChildren(...content);
    $('agenda-review-error').textContent=''; $('agenda-review-confirm').textContent=label;
    $('agenda-review-confirm').onclick=async()=>{
      try {
        if(await mutate(payload)) { $('agenda-review-dialog').close(); done(); }
      } catch(error) {
        $('agenda-review-error').textContent=error.message+(error.status===409?' Cerrá y revisá nuevamente.':' Podés reintentar esta misma solicitud.');
        await refresh().catch(()=>{});
      }
    };
    $('agenda-review-dialog').showModal();
  }
  function configure(t) {
    if(getState().role!=='admin') return;
    if(t.enabled) { message('Pausá la agenda antes de editarla.',true); return; }
    const schedule=t.schedule, revision=getState().revision, today=dayString(now());
    $('agenda-title').textContent=t.name; $('agenda-zone').textContent='Horarios de '+getState().city.name+' · '+getState().city.timezone;
    $('agenda-from').value=schedule?.from || today;
    $('agenda-until').value=schedule?.until || today;
    $('agenda-start').value=schedule?.time || '07:45'; $('agenda-end').value=schedule?.endTime || '08:15';
    $('agenda-exceptions').value=(schedule?.exceptions || []).join('\n'); $('agenda-exception-date').value='';
    $('agenda-fields').disabled=false; $('agenda-error').textContent=''; $('agenda-save').textContent='Guardar agenda desactivada';
    const weekdays=$('agenda-weekdays'); weekdays.replaceChildren();
    days.forEach((name,index)=>{
      const label=el('label','weekday'), check=el('input'); check.type='checkbox';check.value=String(index);check.checked=(schedule?.weekdays||[0,1,2,3,4]).includes(index);
      label.append(check,el('span','',name.slice(0,3)));check.setAttribute('aria-label',name);weekdays.append(label);
    });
    let payload;
    $('agenda-form').onsubmit=async event=>{
      event.preventDefault();
      try {
        const selected=[...weekdays.querySelectorAll('input:checked')].map(c=>Number(c.value));
        if(!payload && !selected.length) throw new Error('Elegí al menos un día de la semana.');
        payload ||= request('save_map_schedule',{revision,templateId:t.id,templateVersion:t.version,schedule:{
          from:$('agenda-from').value,until:$('agenda-until').value,time:$('agenda-start').value,endTime:$('agenda-end').value,
          weekdays:selected,exceptions:$('agenda-exceptions').value.split(/[\n,;]+/).map(d=>d.trim()).filter(Boolean)}});
        $('agenda-fields').disabled=true;
        if(await mutate(payload)) { $('agenda-dialog').close();view('agenda');message('Agenda guardada y desactivada. Revisá sus fechas y pulsá Habilitar para publicar automáticamente.'); }
      } catch(error) {
        $('agenda-error').textContent=error.message+(error.status===409?' Cerrá y revisá el formulario antes de guardar nuevamente.':'');
        if(!payload) $('agenda-fields').disabled=false;
        $('agenda-save').textContent='Reintentar guardado'; await refresh().catch(()=>{});
      }
    };
    $('agenda-dialog').showModal();
  }
  $('agenda-add-exception').onclick=()=>{
    const value=$('agenda-exception-date').value; if(!value)return;
    $('agenda-exceptions').value=[...new Set([...$('agenda-exceptions').value.split('\n').map(s=>s.trim()).filter(Boolean),value])].sort().join('\n');
    $('agenda-exception-date').value='';
  };
  async function enable(t) {
    try {
      const data=await api('/api/agenda/'+encodeURIComponent(t.id));
      if(data.templateVersion!==t.version) throw new Error('La plantilla cambió. Actualizá y revisá nuevamente.');
      if(data.problem) throw new Error(data.problem);
      if(!data.schedule || !data.occurrences.length) throw new Error('No hay fechas pendientes. Revisá la agenda.');
      const schema=el('div','summary-map');
      schematic(t.segments.map(s=>({road:s,direction:s.direction==='BOTH_DIRECTIONS'?'BOTH':'AB'})),schema);
      const text=el('div','info-box','Al habilitar, el servidor incorporará al feed las fechas vigentes y de los próximos 7 días. Continuará al consultar el feed, aunque cierres el panel. Esto no confirma aceptación de Waze.');
      const list=el('ul','summary-list');
      data.occurrences.slice(0,8).forEach(o=>list.append(el('li','',(o.start<=data.serverTime?'EN CURSO · ':'')+format(o.start)+' → '+format(o.end))));
      const streets=el('ul','summary-list');t.segments.forEach(s=>streets.append(el('li','',s.street+' · '+s.from+' → '+s.to+(s.direction==='BOTH_DIRECTIONS'?' · ambos sentidos':''))));
      confirm('¿Habilitar «'+t.name+'»?',[
        schema,el('p','',brief(data.schedule)),el('p','',data.schedule.from+' a '+data.schedule.until+' · '+data.schedule.timezone),
        el('p','',data.schedule.exceptions.length+' fecha(s) excluida(s)'),text,el('h3','','Primeras fechas pendientes'),list,el('h3','','Calles y sentidos'),streets
      ],request('enable_map_schedule',{revision:data.revision,templateId:t.id,templateVersion:t.version}),'Habilitar y publicar agenda',()=>message('Agenda habilitada. Sus fechas vigentes y próximas están en el feed; Waze debe leerlas y validarlas.'));
    } catch(error) { fail(error); }
  }
  function pause(t) {
    confirm('¿Pausar «'+t.name+'»?',[
      el('div','info-box','Se cancelarán en el feed las fechas futuras generadas por esta agenda. Los cierres que ya comenzaron y los operativos manuales seguirán vigentes. Podés habilitarla nuevamente más adelante.')
    ],request('pause_map_schedule',{templateId:t.id,templateVersion:t.version}),'Pausar y cancelar próximas fechas',()=>message('Agenda pausada. Se conservaron los cierres en curso y los manuales.'));
  }
  function suspend(t,data,ds,done) {
    confirm('¿Suspender el '+ds+'?',[
      el('p','',t.name),el('div','info-box','Solo se excluye esta fecha de la agenda. Si ya estaba publicada, se actualizará el feed. No cambia otros días ni operativos manuales.')
    ],request('exclude_map_date',{revision:data.revision,templateId:t.id,templateVersion:t.version,date:ds}),'Suspender esta fecha',()=>{message('Fecha suspendida. El resto de la agenda se conserva.');done();});
  }
  async function open(t,month) {
    try {
      const data=await api('/api/agenda/'+encodeURIComponent(t.id));
      if(!data.schedule) { if(getState().role==='admin') configure(t); else throw new Error('La plantilla todavía no tiene agenda.'); return; }
      $('agenda-dates-title').textContent=t.name;
      const body=$('agenda-dates-body');body.replaceChildren();
      body.append(el('span','badge '+(data.enabled?'template-badge':'future'),data.enabled?'AGENDA HABILITADA':'AGENDA DESACTIVADA'),el('p','',brief(data.schedule)),el('p','field-help',data.schedule.from+' a '+data.schedule.until+' · '+data.schedule.timezone));
      if(data.problem) body.append(el('p','error',data.problem));
      month ||= (data.schedule.from>dayString(data.serverTime)?data.schedule.from:dayString(data.serverTime)).slice(0,7);
      const [year,num]=month.split('-').map(Number), first=new Date(Date.UTC(year,num-1,1)), last=new Date(Date.UTC(year,num,0)).getUTCDate();
      const nav=el('div','calendar-nav');
      const move=delta=>{ const d=new Date(Date.UTC(year,num-1+delta,1));open(t,d.toISOString().slice(0,7)); };
      nav.append(button('‹',()=>move(-1)),el('strong','',new Intl.DateTimeFormat('es-AR',{month:'long',year:'numeric',timeZone:'UTC'}).format(first)),button('›',()=>move(1)));
      nav.firstChild.setAttribute('aria-label','Mes anterior');nav.lastChild.setAttribute('aria-label','Mes siguiente');body.append(nav);
      const grid=el('div','calendar-grid');
      days.forEach(name=>grid.append(el('span','calendar-weekday',name.slice(0,2))));
      for(let i=0;i<(first.getUTCDay()+6)%7;i++) grid.append(el('span'));
      for(let d=1;d<=last;d++) {
        const ds=month+'-'+String(d).padStart(2,'0'), row=data.occurrences.find(o=>o.date===ds), excluded=data.schedule.exceptions.includes(ds);
        const cell=button(String(d),()=>suspend(t,data,ds,()=>open(t,month)),'calendar-day'+(row?' planned':'')+(excluded?' excluded':'')+(ds===dayString(data.serverTime)?' today':''));
        cell.disabled=getState().role==='viewer' || !row || row.start<=data.serverTime;cell.setAttribute('aria-label',ds+(excluded?' · suspendida':row?' · prevista'+(getState().role==='viewer'?'':' ; suspender fecha'):' · sin cierre pendiente'));
        grid.append(cell);
      }
      body.append(grid,el('p','field-help','Verde: fecha prevista · Ámbar: excepción. '+(getState().role==='viewer'?'Acceso de solo lectura.':'Tocá una fecha futura para suspenderla, con confirmación.')));
      const list=el('div','agenda-upcoming');list.append(el('h3','','Próximas fechas'));
      data.occurrences.slice(0,8).forEach(o=>{const row=el('div','agenda-date');row.append(el('span','',format(o.start)+' → '+format(o.end)));if(o.start>data.serverTime)row.append(button('Suspender',()=>suspend(t,data,o.date,()=>open(t,month)),'text-button'));else row.append(el('span','badge','EN CURSO'));list.append(row);});
      if(!data.occurrences.length) list.append(el('p','field-help','No quedan fechas pendientes.'));
      body.append(list);
      if(!$('agenda-dates-dialog').open) $('agenda-dates-dialog').showModal();
    } catch(error) { fail(error); }
  }
  function render() {
    const state=getState(), catalog=getCatalog(), templates=state.templates.filter(t=>t.source==='map'&&!t.archived);
    const key=JSON.stringify([state.role,catalog?.version,templates]);if(key===renderKey)return;renderKey=key;
    const list=$('agendas');list.replaceChildren();
    if(!templates.length) {
      const empty=el('div','empty-state','▦');empty.append(el('strong','','Primero, una plantilla'),el('p','','Guardá las calles desde Plantillas y después elegí cuándo deben cerrarse.'));list.append(empty);return;
    }
    for(const t of templates) {
      const card=el('article','template-card'), stale=catalog&&t.catalogVersion!==catalog.version;
      card.append(el('span','badge '+(t.enabled?'template-badge':'future'),t.enabled?'AUTOMÁTICA · HABILITADA':t.schedule?'GUARDADA · DESACTIVADA':'SIN AGENDA'),el('h3','',t.name));
      if(t.schedule) card.append(el('p','',brief(t.schedule)),el('p','field-help',t.schedule.from+' → '+t.schedule.until+' · '+t.schedule.exceptions.length+' excepciones'));
      else card.append(el('p','','Elegí los días y una franja horaria para esta plantilla.'));
      if(stale)card.append(el('p','warning','El mapa cambió. No se generarán nuevas fechas hasta revisar la plantilla.'));
      const actions=el('div','run-actions');
      if(t.schedule) actions.append(button('Ver calendario',()=>open(t)));
      if(state.role==='admin') {
        if(t.enabled) actions.append(button('Pausar agenda',()=>pause(t),'danger'));
        else {
          actions.append(button(t.schedule?'Editar agenda':'Configurar agenda',()=>configure(t)));
          if(t.schedule) {const b=button('Revisar y habilitar →',()=>enable(t),'primary');b.disabled=!!stale;actions.append(b);}
        }
      }
      card.append(actions);list.append(card);
    }
  }
  return {render,open,configure};
};
