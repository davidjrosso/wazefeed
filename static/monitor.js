'use strict';
function ViaMonitor({$,el,button,api,getState,getMap,view,message,format,prepare}) {
  let data,group,focusId,saving=false,loading=false,shown=40;
  const statuses={pending:'Pendiente',verified:'Verificado',assigned:'Derivado',progress:'En atención',resolved:'Resuelto localmente'};
  const nav=button('Monitoreo',()=>{},'nav-tab');nav.dataset.view='monitor';nav.id='monitor-nav';$('users-nav').before(nav);
  $('monitor-refresh').before(button('Ver todas en mapa',()=>fitAlerts(),'text-button'));
  const normalize=s=>s.normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase();
  const kind=a=>a.subtype.includes('POT_HOLE')?'pothole':a.subtype.includes('FLOOD')?'flood':a.type==='ACCIDENT'?'ACCIDENT':a.type==='ROAD_CLOSED'?'ROAD_CLOSED':['HAZARD','WEATHERHAZARD'].includes(a.type)?'HAZARD':'other';
  const labels={pothole:'Bache',flood:'Inundación',ACCIDENT:'Accidente',ROAD_CLOSED:'Cierre reportado',HAZARD:'Peligro',other:'Otro reporte'};
  const colors={pothole:'#b77721',flood:'#2479b1',ACCIDENT:'#cc514f',ROAD_CLOSED:'#953f71',HAZARD:'#cd8430',other:'#5b6f9b'};
  const symbol={pothole:'◉',flood:'≈',ACCIDENT:'!',ROAD_CLOSED:'⊖',HAZARD:'!',other:'•'};
  function filtered(){
    const search=normalize($('monitor-search').value.trim()),type=$('monitor-type').value,status=$('monitor-status').value,presence=$('monitor-presence').value;
    return (data?.alerts||[]).filter(a=>(!search||normalize(a.street+' '+a.description).includes(search))&&(!type||kind(a)===type)&&(!status||a.case.status===status)&&(presence==='all'||(presence==='present'?a.present:!a.present)));
  }
  function draw(){
    const map=getMap();if(!map)return;
    let attribution=$('monitor-attribution');if(!attribution){attribution=el('div','monitor-attribution');attribution.id='monitor-attribution';const link=el('a','','Waze App');link.href=matchMedia('(max-width:750px)').matches?'https://m.waze.com':'https://www.waze.com';link.target='_blank';link.rel='noopener';attribution.append(document.createTextNode('Data provided by '),link,document.createTextNode('. Learn more at Waze.com.'));document.querySelector('.map-shell').append(attribution);}
    attribution.hidden=!$('monitor-layer').checked;
    if(!group)group=L.layerGroup().addTo(map);
    group.clearLayers();if(!$('monitor-layer').checked)return;
    for(const a of filtered().slice(0,500)){
      const marker=L.circleMarker(a.point,{radius:9,weight:3,color:'#fff',fillColor:colors[kind(a)],fillOpacity:a.present?.95:.45}).addTo(group);
      marker.bindTooltip(el('span','',labels[kind(a)]+' · '+(a.street||'Calle sin nombre')+' · Waze'));
      marker.on('click',()=>{view('monitor',false);open(a.id);});
    }
  }
  function render(){
    if(!data)return;
    const meta=data.meta,health=$('monitor-health');health.classList.toggle('stale',meta.stale);
    health.textContent=meta.lastSuccess?(meta.stale?'Datos desactualizados · ':'Última lectura correcta · ')+format(meta.lastSuccess):'Sin datos recibidos. El resto del panel sigue funcionando.';
    if(meta.invalidCount)health.append(el('small','',' Lectura parcial: '+meta.invalidCount+' registro(s) inválido(s). Se conservan las observaciones anteriores.'));
    if(meta.outsideCount)health.append(el('small','',' '+meta.outsideCount+' reporte(s) fuera del área operativa configurada.'));
    const rows=filtered(),present=data.alerts.filter(a=>a.present).length;
    nav.textContent='Monitoreo · '+present+(meta.stale?' ⚠':'');
    $('monitor-count').textContent=rows.length+' reporte(s) · '+rows.filter(a=>a.case.status!=='resolved').length+' sin resolver localmente.'+(rows.length>500?' El mapa muestra los primeros 500.':'')+(data.truncated?' Se muestran los 5000 registros más recientes del historial.':'');
    const list=$('monitor-list');list.replaceChildren();
    if(!rows.length)list.append(el('div','monitor-empty',data.meta.lastSuccess?'No hay reportes para estos filtros.':'Esperando la primera lectura del feed.'));
    for(const a of rows.slice(0,shown)){
      const card=el('article','monitor-card'+(!a.present?' absent':'')),head=el('div','monitor-card-head');
      const icon=el('span','monitor-symbol type-'+kind(a),symbol[kind(a)]);head.append(icon,el('span','',labels[kind(a)]),el('span','monitor-state status-'+a.case.status,statuses[a.case.status]));
      card.append(head,el('h3','',a.street||'Calle sin nombre'),el('p','', 'Reportado '+format(a.publishedAt)),el('p','field-help',a.present?'Observado en la última lectura de Waze':'Ya no observado · no implica resolución'));
      if(a.case.area)card.append(el('p','field-help',a.case.area));
      const actions=el('div','run-actions');actions.append(button('Ver en mapa',()=>{getMap().setView(a.point,18);}),button('Ver ficha →',()=>open(a.id),'primary'));card.append(actions);list.append(card);
    }
    $('monitor-more').hidden=shown>=rows.length;draw();
  }
  function fitAlerts(){const rows=filtered().slice(0,500);if(rows.length&&getMap())getMap().fitBounds(L.latLngBounds(rows.map(a=>a.point)),{padding:[70,70],maxZoom:17});}
  async function load(fit=false){
    if(loading)return;loading=true;$('monitor-refresh').disabled=true;
    try {data=await api('/api/monitor');render();if(fit===true)fitAlerts();}
    catch(e){$('monitor-health').textContent='No se pudo actualizar Monitoreo. Los datos anteriores pueden estar desactualizados.';$('monitor-health').classList.add('stale');}
    finally{loading=false;$('monitor-refresh').disabled=false;}
  }
  function distance(a,b){const rad=Math.PI/180,dy=(a[0]-b[0])*rad,dx=(a[1]-b[1])*rad;return 12742000*Math.asin(Math.min(1,Math.sqrt(Math.sin(dy/2)**2+Math.cos(a[0]*rad)*Math.cos(b[0]*rad)*Math.sin(dx/2)**2)));}
  async function open(id){
    const a=data?.alerts.find(a=>a.id===id);if(!a)return;
    focusId=id;getMap().setView(a.point,18);
    const detail=$('monitor-detail');detail.replaceChildren();$('monitor-title').textContent=labels[kind(a)]+' · '+(a.street||'Sin nombre');
    const row=(label,value)=>{const r=el('div','summary-row');r.append(el('span','',label),el('strong','',value));detail.append(r);};
    row('Reportado',format(a.publishedAt));row('Primera observación local',format(a.firstSeen));row('Última observación local',format(a.lastSeen));
    row('En Waze',a.present?'En la última lectura':'Ya no observado / fuera del historial retenido');
    row('Fiabilidad',a.reliability===null?'No informada':a.reliability+' / 10');row('Confirmaciones positivas',a.thumbsUp===null?'No informadas':String(a.thumbsUp));
    row('Confianza informada',a.confidence===null?'No informada':String(a.confidence));
    if(a.description)detail.append(el('p','monitor-description',a.description));
    detail.append(el('p','field-help','Tipo original: '+a.type+' / '+a.subtype));
    if(data.meta.stale)detail.append(el('p','warning','La fuente está desactualizada. Verificá la situación antes de actuar.'));
    const related=data.alerts.filter(b=>b.id!==id&&b.subtype===a.subtype&&b.type===a.type&&b.present===a.present&&distance(a.point,b.point)<40);
    $('monitor-related').hidden=!related.length;$('monitor-related').textContent=related.length+' reporte(s) del mismo tipo a menos de 40 metros. Podrían estar relacionados; no se fusionaron ni se dieron por duplicados.';
    $('monitor-case-status').value=a.case.status;$('monitor-area').value=a.case.area;$('monitor-note').value=a.case.note;
    const viewer=getState().role==='viewer';$('monitor-case-fields').disabled=viewer;$('monitor-save').hidden=viewer;$('monitor-prepare').hidden=viewer;
    $('monitor-error').textContent='';$('monitor-history').textContent='Cargando historial…';
    const dialog=$('monitor-dialog');if(!dialog.open)dialog.showModal();
    const version=a.case.version;
    $('monitor-prepare').onclick=()=>{dialog.close();prepare(a);};
    $('monitor-form').onsubmit=async e=>{
      e.preventDefault();if(saving||viewer)return;
      saving=true;dialog.querySelectorAll('button').forEach(b=>b.disabled=true);$('monitor-error').textContent='';
      try {
        data=await api('/api/monitor',{id,version,status:$('monitor-case-status').value,area:$('monitor-area').value,note:$('monitor-note').value});
        dialog.close();render();message('Seguimiento guardado. No se modificó Waze ni se publicó ningún cierre.');
      }catch(error){$('monitor-error').textContent=error.message+' Si la respuesta se interrumpió, cerrá la ficha y actualizá para comprobar el resultado.';}
      finally{saving=false;dialog.querySelectorAll('button').forEach(b=>b.disabled=false);}
    };
    try{
      const result=await api('/api/monitor/'+encodeURIComponent(id));if(focusId!==id)return;
      const history=$('monitor-history');history.replaceChildren();
      for(const h of result.history){const p=el('p','field-help',format(h.at)+' · '+h.actor+' · '+statuses[h.status]+(h.area?' · '+h.area:''));if(h.note)p.append(el('span','monitor-history-note',h.note));history.append(p);}
      if(!result.history.length)history.textContent='Todavía no hay acciones municipales registradas.';
    }catch(e){if(focusId===id)$('monitor-history').textContent='No se pudo cargar el historial.';}
  }
  $('monitor-dialog').addEventListener('cancel',e=>{if(saving)e.preventDefault();});
  $('monitor-layer').onchange=draw;$('monitor-refresh').onclick=load;
  for(const id of ['monitor-type','monitor-status','monitor-presence'])$(id).onchange=()=>{shown=40;render();};
  $('monitor-search').oninput=()=>{shown=40;render();};$('monitor-more').onclick=()=>{shown+=40;render();};
  return {load,draw};
}
