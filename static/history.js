'use strict';
(() => {
  const $ = id => document.getElementById(id), base = document.body.dataset.base;
  const el = (tag, cls, text) => {const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;};
  const zone='America/Argentina/Cordoba', labels={pothole:'Bache',flood:'Inundación',accident:'Accidente',closure:'Cierre reportado',hazard:'Peligro',other:'Otro reporte'};
  const date = stamp => new Intl.DateTimeFormat('es-AR',{timeZone:zone,dateStyle:'short',timeStyle:'short'}).format(new Date(stamp*1000));
  const today = () => {const p=new Intl.DateTimeFormat('en-CA',{timeZone:zone,year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(new Date());return ['year','month','day'].map(k=>p.find(v=>v.type===k).value).join('-');};
  let data, applied, offset=0, loading=false, detailId, detailCursor, detailMap, detailMarker, detailGeneration=0;
  async function api(path) {
    const controller=new AbortController(), timer=setTimeout(()=>controller.abort(),20000);
    try {
      const r=await fetch(base+path,{credentials:'same-origin',signal:controller.signal});
      if(r.status===401){$('history-auth').hidden=false;$('history-results').hidden=true;$('history-dialog').close();throw Error('Tu sesión terminó. Ingresá nuevamente a Vía.');}
      const result=await r.json();if(!r.ok)throw Error(result.error||'No se pudo consultar el archivo.');return result;
    } catch(e){if(e.name==='AbortError')throw Error('La consulta demoró demasiado. Elegí un período más corto o intentá nuevamente.');throw e;}
    finally{clearTimeout(timer);}
  }
  function preset(days) {
    const end=today(), start=new Date(end+'T12:00:00Z');start.setUTCDate(start.getUTCDate()-days+1);
    $('history-from').value=start.toISOString().slice(0,10);$('history-to').value=end;
    document.querySelectorAll('[data-days]').forEach(b=>b.classList.toggle('active',Number(b.dataset.days)===days));
  }
  function params(){return new URLSearchParams({from:$('history-from').value,to:$('history-to').value,type:$('history-type').value,street:$('history-street').value.trim(),reliability:$('history-reliability').value});}
  function message(text){$('history-error').textContent=text;$('history-error').hidden=!text;}
  async function load(page=0, retain=false) {
    if(loading)return;
    loading=true;message('');$('history-prev').disabled=true;$('history-next').disabled=true;
    $('history-results').setAttribute('aria-busy','true');
    const query=retain&&applied?new URLSearchParams(applied):params();query.set('offset',page);
    $('history-filters').querySelectorAll('input,select,button').forEach(n=>n.disabled=true);
    try {
      const result=await api('/api/history?'+query);data=result;offset=page;applied=new URLSearchParams(query);applied.delete('offset');
      $('history-auth').hidden=true;$('history-results').hidden=false;render();
      window.history.replaceState(null,'',base+'/history?'+applied);
    } catch(e){message(e.message||'No se pudo consultar el archivo.');$('history-results').hidden=true;$('archive-status').textContent='Consulta no disponible';$('archive-status').classList.add('stale');}
    finally{loading=false;$('history-filters').querySelectorAll('input,select,button').forEach(n=>n.disabled=false);$('history-results').removeAttribute('aria-busy');}
  }
  function horizontal(id, rows){
    const root=$(id);root.replaceChildren();const max=Math.max(1,...rows.map(r=>r.count));
    if(!rows.length){root.append(el('p','history-help','Sin reportes para estos filtros.'));return;}
    for(const r of rows){const row=el('div','horizontal-row'), label=el('span','',r.label), track=el('div','horizontal-track'), fill=el('div','horizontal-fill');label.title=r.label;fill.style.width=(100*r.count/max)+'%';track.append(fill);row.append(label,el('strong','',r.count),track);root.append(row);}
  }
  function trend(){
    const root=$('history-trend');root.replaceChildren();root.style.minWidth=Math.max(400,data.series.length*31)+'px';
    const max=Math.max(1,...data.series.flatMap(s=>[s.observed,s.published]));
    $('trend-title').textContent=data.series.length===24&&data.filters.from===data.filters.to?'Evolución por hora':'Evolución por día';
    for(const b of data.series){
      const noReading=b.readings===b.failed, col=el('div','trend-column'+(noReading?' no-reading':''));
      const title=b.label+': '+b.observed+' observados; '+b.published+' publicados. '+(noReading?'Sin lecturas correctas.':b.readings+' lecturas, '+b.failed+' fallidas, '+b.partial+' parciales.');
      col.title=title;col.setAttribute('aria-label',title);
      for(const [key,cls] of [['observed','observed-bar'],['published','published-bar']]){
        const bar=el('div','trend-bar '+cls);bar.style.height=(b[key]/max*86)+'%';if(b[key])bar.append(el('span','',b[key]));col.append(bar);
      }
      col.append(el('span','trend-label',b.label));root.append(col);
    }
    root.setAttribute('aria-label','Reportes observados y publicados por '+(data.filters.from===data.filters.to?'hora':'día')+'. Total de reportes distintos: '+data.total+'. Las franjas rayadas no tienen lecturas correctas.');
  }
  function render(){
    const {meta,metrics:m,coverage:c}=data;
    $('archive-status').textContent=(meta.stale?'Fuente desactualizada':'Archivo en funcionamiento')+' · Última lectura '+(meta.lastSuccess?date(meta.lastSuccess):'sin datos');$('archive-status').classList.toggle('stale',meta.stale);
    $('history-scope').textContent='Observaciones del '+data.filters.from.split('-').reverse().join('/')+' al '+data.filters.to.split('-').reverse().join('/')+'. Archivo detallado desde '+(meta.startedAt?date(meta.startedAt):'la primera lectura')+'. '+meta.reports+' reportes conservados en total, incluidos los importados.';
    $('metric-unique').textContent=m.unique.toLocaleString('es-AR');$('metric-published').textContent=m.published.toLocaleString('es-AR');
    $('metric-reliability').textContent=m.averageReliability===null?'—':m.averageReliability.toLocaleString('es-AR')+' / 10';
    $('metric-reliability-help').textContent=m.reliabilityKnown+' de '+m.unique+' reportes con puntuación informada.';
    $('metric-readings').textContent=(c.successful+c.failed).toLocaleString('es-AR');$('metric-readings-help').textContent=c.failed+' fallidas · '+c.partial+' parciales. Todas las calles.';
    const coverage=[];
    if(c.beforeArchive)coverage.push('El período comienza antes del archivo detallado; no hay lecturas anteriores para reconstruirlo.');
    if(c.gaps)coverage.push(c.gaps+' intervalo(s) de más de 6 minutos sin lectura correcta; el mayor dura '+c.largestGapMinutes+' minutos.');
    if(!c.successful)coverage.push('No hay lecturas correctas en este período. Esto no significa que no haya habido reportes.');
    else coverage.push(m.firstDetected+' reportes detectados por primera vez durante el período (excluye la importación inicial).');
    if(c.partial)coverage.push('Hay lecturas parciales: algunos registros no pudieron interpretarse.');
    $('history-coverage').textContent=coverage.join(' ');$('history-coverage').classList.toggle('caution',c.beforeArchive||c.gaps>0||!c.successful||c.partial>0);
    trend();horizontal('history-types',data.types);horizontal('history-streets',data.streets);
    const rel=$('reliability-distribution');rel.replaceChildren();const max=Math.max(1,...data.reliability.map(r=>r.count));
    for(const r of data.reliability){const col=el('div','reliability-column'),bar=el('div','trend-bar');bar.style.height=(r.count/max*87)+'%';if(r.count)bar.append(el('span','',r.count));col.title=r.score+'/10: '+r.count+' reportes';col.append(bar,el('span','trend-label',r.score));rel.append(col);}
    $('history-reliability-note').textContent=(m.unique-m.reliabilityKnown)+' reportes sin fiabilidad informada. Se usa el último valor observado en el período.';
    const tbody=$('history-rows');tbody.replaceChildren();
    for(const r of data.reports){
      const tr=el('tr'),street=el('td','',r.street||'Calle sin nombre');street.append(el('small','',labels[r.category]+(r.imported?' · importado':'')));tr.append(street,el('td','',date(r.publishedAt)),el('td','',date(r.periodLast)));
      const score=el('td');score.append(el('span','score-pill',r.reliability===null?'Sin informar':r.reliability+' / 10'));tr.append(score,el('td','',r.thumbsUp===null?'Sin informar':r.thumbsUp));
      const action=el('td'),b=el('button','','Ver historia →');b.type='button';b.setAttribute('aria-label','Ver historia de '+(r.street||'calle sin nombre'));b.onclick=()=>openDetail(r.id);action.append(b);tr.append(action);tbody.append(tr);
    }
    $('history-empty').hidden=data.total>0;$('history-empty').textContent=c.successful?'No se observaron reportes que coincidan con estos filtros.':'No hay lecturas correctas para analizar este período.';
    $('history-table-count').textContent=data.total+' reportes distintos';$('history-page-count').textContent=data.total?`${offset+1}–${Math.min(offset+data.limit,data.total)} de ${data.total}`:'Sin resultados';
    $('history-prev').disabled=offset===0;$('history-next').disabled=offset+data.limit>=data.total;
  }
  const eventLabels={new:'Detectado por primera vez',changed:'Cambió la información',absent:'Dejó de observarse',reappeared:'Volvió a observarse',imported:'Importado de la caché anterior'};
  async function openDetail(id,more=false){
    const generation=++detailGeneration;detailId=id;$('detail-more').disabled=true;$('detail-error').textContent='';
    if(!more){detailCursor=null;$('detail-title').textContent='Cargando reporte…';$('detail-content').hidden=true;$('detail-events').replaceChildren();if(!$('history-dialog').open)$('history-dialog').showModal();}
    const query=new URLSearchParams(applied);if(more)query.set('after',detailCursor);
    try{
      const result=await api('/api/history/'+encodeURIComponent(id)+'?'+query);
      if(generation!==detailGeneration||!$('history-dialog').open)return;
      const r=result.report;$('detail-title').textContent=(r.street||'Calle sin nombre')+' · '+labels[r.category];$('detail-content').hidden=false;
      if(!more){
        const facts=$('detail-facts');facts.replaceChildren();
        for(const [label,value] of [['Publicado en Waze',date(r.publishedAt)],['Primera observación conservada',date(r.firstSeen)+(r.imported?' · importada':'')],['Primera observación en este período',date(r.periodFirst)],['Última observación en este período',date(r.periodLast)],['Fiabilidad',r.reliability===null?'No informada':r.reliability+' / 10'],['Confianza informada',r.confidence===null?'No informada':r.confidence],['Confirmaciones positivas',r.thumbsUp===null?'No informadas':r.thumbsUp],['Lecturas con este reporte',r.observations]]){const div=el('div');div.append(el('small','',label),el('strong','',value));facts.append(div);}
        if(r.description){const div=el('div');div.append(el('small','','Descripción'),el('span','',r.description));facts.append(div);}
        if(!detailMap){detailMap=L.map('detail-map',{scrollWheelZoom:false}).setView(r.point,17);L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:'© OpenStreetMap contributors · Datos de Waze'}).addTo(detailMap);}
        detailMap.setView(r.point,17);if(detailMarker)detailMarker.remove();detailMarker=L.circleMarker(r.point,{radius:10,color:'#087f78',fillOpacity:.7}).addTo(detailMap);requestAnimationFrame(()=>detailMap.invalidateSize());
        const municipal=$('detail-municipal');municipal.replaceChildren();const states={pending:'Pendiente',verified:'Verificado',assigned:'Derivado',progress:'En atención',resolved:'Resuelto localmente'};
        for(const h of result.municipalHistory){const div=el('div','',h.actor+' · '+(states[h.status]||h.status));div.append(el('small','',date(h.at)+(h.area?' · '+h.area:'')),el('p','',h.note));municipal.append(div);}
        if(!result.municipalHistory.length)municipal.append(el('p','history-help','Todavía no hay acciones municipales registradas.'));
      }
      for(const t of result.events){const div=el('div','',eventLabels[t.kind]||t.kind);div.append(el('small','',date(t.at)),el('span','',`Fiabilidad: ${t.body.reliability??'sin informar'} · Confianza: ${t.body.confidence??'sin informar'} · Confirmaciones: ${t.body.thumbsUp??'sin informar'}`));$('detail-events').append(div);}
      if(!more&&!result.events.length)$('detail-events').append(el('p','history-help','Sin cambios registrados en este período. Las lecturas repetidas conservan el mismo reporte.'));
      detailCursor=result.nextCursor;$('detail-more').hidden=!result.hasMore;
    }catch(e){if(generation===detailGeneration)$('detail-error').textContent=e.message||'No se pudo abrir el detalle.';}
    finally{if(generation===detailGeneration)$('detail-more').disabled=false;}
  }
  $('detail-close').onclick=()=>{$('history-dialog').close();detailGeneration++;};$('history-dialog').addEventListener('cancel',()=>detailGeneration++);
  $('detail-more').onclick=()=>openDetail(detailId,true);
  $('history-filters').onsubmit=e=>{e.preventDefault();load();};
  document.querySelectorAll('[data-days]').forEach(b=>b.onclick=()=>{preset(Number(b.dataset.days));load();});
  $('history-prev').onclick=()=>load(Math.max(0,offset-50),true);$('history-next').onclick=()=>load(offset+50,true);
  $('history-from').max=today();$('history-to').max=today();preset(7);
  const initial=new URLSearchParams(location.search);
  for(const [key,id] of [['from','history-from'],['to','history-to'],['type','history-type'],['street','history-street'],['reliability','history-reliability']])if(initial.has(key))$(id).value=initial.get(key);
  load();
})();
