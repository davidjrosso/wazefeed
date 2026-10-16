// ==UserScript==
// @name         Cierres municipales - piloto Waze for Cities
// @namespace    municipal-closures-pilot
// @version      0.3.0
// @description  Programa lotes en WME. Revisión explícita. Guardado final siempre manual en Waze.
// @match        https://www.waze.com/editor*
// @match        https://www.waze.com/*/editor*
// @grant        none
// @run-at       document-end
// @noframes
// ==/UserScript==

(()=>{
'use strict';
const VERSION='0.3.0';
const MAX_SEGMENTS=20;
function isWmeLocation(origin,path){return origin==='https://www.waze.com'&&/^\/(?:[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*\/)?editor\/?$/.test(path);}
function requireThat(ok,message){if(!ok)throw Error(message);}
function label(value,name,max=120){requireThat(typeof value==='string' && value.trim().length>0 && value.length<=max && !/[\u0000-\u001f\u007f]/.test(value),`${name}: texto inválido (máximo ${max}).`);return value.trim();}
function profileOf(value){
  const city=label(value.city,'Municipio'),zone=label(value.zone,'Zona horaria');
  try{new Intl.DateTimeFormat('en',{timeZone:zone}).format(0);}catch{throw Error('Zona horaria IANA inválida.');}
  return {city,zone};
}
function partsAt(ms,zone){
  return Object.fromEntries(new Intl.DateTimeFormat('en-CA',{timeZone:zone,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'}).formatToParts(ms).filter(p=>p.type!=='literal').map(p=>[p.type,Number(p.value)]));
}
function wallToInstant(value,zone){
  requireThat(typeof value==='string' && /^\d{4}-\d\d-\d\dT\d\d:\d\d(?::\d\d)?$/.test(value),'Completá fecha y hora.');
  const [y,m,d,h,min,sec=0]=value.split(/[-T:]/).map(Number);
  requireThat(y>=2000 && y<=2100 && m>=1 && m<=12 && d>=1 && h<24 && min<60 && sec<60,'Fecha u hora inválida.');
  const raw=Date.UTC(y,m-1,d,h,min,sec),date=new Date(raw);
  requireThat(date.getUTCDate()===d && date.getUTCMonth()===m-1,'Día inválido.');
  const offsets=new Set();
  for(let delta=-36;delta<=36;delta+=6){const t=raw+delta*3600000,p=partsAt(t,zone);offsets.add(Date.UTC(p.year,p.month-1,p.day,p.hour,p.minute,p.second)-t);}
  const matches=[...offsets].map(offset=>raw-offset).filter(t=>{const p=partsAt(t,zone);return p.year===y&&p.month===m&&p.day===d&&p.hour===h&&p.minute===min&&p.second===sec;});
  requireThat(matches.length===1,matches.length?'Hora ambigua por cambio de horario de verano. Elegí otra hora.':'Hora inexistente por cambio de horario de verano. Elegí otra hora.');
  return matches[0];
}
function displayTime(ms,zone){return new Intl.DateTimeFormat('es-AR',{timeZone:zone,dateStyle:'medium',timeStyle:'short',hour12:false}).format(ms);}
function validIds(ids){requireThat(Array.isArray(ids)&&ids.length>0&&ids.length<=MAX_SEGMENTS&&ids.every(id=>Number.isSafeInteger(id)&&id>0)&&new Set(ids).size===ids.length,`Seleccioná hasta ${MAX_SEGMENTS} tramos guardados, sin duplicados.`);return [...ids];}
function snapshotSegments(sdk,ids){
  return validIds(ids).map(id=>{
    const s=sdk.DataModel.Segments.getById({segmentId:id});requireThat(s&&s.id===id,`Tramo ${id} no cargado. Acercá el mapa y volvé a seleccionar.`);
    requireThat(sdk.DataModel.Segments.hasPermissions({segmentId:id,permission:'EDIT_CLOSURES'})===true,`Sin permiso para gestionar cierres en el tramo ${id}.`);
    requireThat(s.isDrivable===true && s.allowNoDirection===false,`El tramo ${id} no tiene circulación vehicular definida.`);
    requireThat([s.isTwoWay,s.isAtoB,s.isBtoA].every(v=>typeof v==='boolean'),`Sentidos incompletos en ${id}.`);
    requireThat(s.isTwoWay?s.isAtoB===s.isBtoA:s.isAtoB!==s.isBtoA,`Sentidos ambiguos en ${id}.`);
    requireThat(Number.isSafeInteger(s.fromNodeId)&&s.fromNodeId>0&&Number.isSafeInteger(s.toNodeId)&&s.toNodeId>0&&s.fromNodeId!==s.toNodeId,`Extremos incompletos o circulares en ${id}.`);
    requireThat(s.geometry?.type==='LineString'&&Array.isArray(s.geometry.coordinates)&&s.geometry.coordinates.length>=2&&s.geometry.coordinates.length<=2000,`Geometría incompleta en ${id}.`);
    const coordinates=s.geometry.coordinates.map(p=>{requireThat(Array.isArray(p)&&p.length===2&&p.every(Number.isFinite)&&Math.abs(p[0])<=180&&Math.abs(p[1])<=90,`Coordenadas inválidas en ${id}.`);return [...p];});
    const a=sdk.DataModel.Segments.getAddress({segmentId:id});
    return {id,street:a?.street?.name||'(sin nombre)',city:a?.city?.name||'(sin ciudad)',country:a?.country?.name||'',
      fromNodeId:s.fromNodeId,toNodeId:s.toNodeId,directions:s.isTwoWay?[true,false]:[s.isAtoB],
      coordinates,hasRestrictions:!!s.hasRestrictions};
  });
}
function ensureEditorClean(sdk){
  requireThat(!sdk.Editing.isPracticeModeOn()&&!sdk.Editing.isSnapshotModeOn(),'Usá el editor normal con tu cuenta autorizada.');
  requireThat(sdk.Editing.isEditingAllowed()===true,'Waze no permite editar en este momento.');
  requireThat(sdk.Editing.getUnsavedChangesCount()===0,'Hay cambios pendientes en Waze. Guardalos o deshacelos manualmente antes de continuar.');
  requireThat(!sdk.Editing.isDrawingInProgress(),'Terminá el dibujo o la edición en curso.');
}
function readCurrent(sdk){ensureEditorClean(sdk);const s=sdk.Editing.getSelection();requireThat(s?.objectType==='segment','Seleccioná calles en el mapa de Waze.');return snapshotSegments(sdk,s.ids);}
function closureTime(value,zone){
  requireThat(typeof value==='string','Hay un cierre cargado sin fechas; revisalo en Waze.');
  if(/(?:Z|[+-]\d\d:\d\d)$/.test(value)){const n=Date.parse(value);requireThat(Number.isFinite(n),'Fecha de cierre existente inválida.');return n;}
  return wallToInstant(value.replace(' ','T'),zone);
}
function checkConflicts(sdk,operations,zone){
  const ids=new Set(operations.map(o=>o.segmentId));
  for(const c of sdk.DataModel.RoadClosures.getAll()){
    if(!ids.has(c.segmentId))continue;
    const start=closureTime(c.startDate,zone),end=closureTime(c.endDate,zone);
    requireThat(end>=start,'Un cierre cargado tiene fechas inconsistentes. Revisalo en Waze.');
    requireThat(typeof c.isForward==='boolean','Un cierre existente no tiene sentido definido.');
    requireThat(!operations.some(o=>o.segmentId===c.segmentId&&o.isForward===c.isForward&&o.startDate<end&&start<o.endDate),`Superposición con un cierre cargado en el tramo ${c.segmentId}. Revisalo en Waze.`);
  }
}
function makePlan(sdk,input,now=Date.now()){
  ensureEditorClean(sdk);
  const profile=profileOf(input.profile),description=label(input.description,'Motivo',39);
  const startMode=input.startMode??'scheduled';
  requireThat(['now','scheduled'].includes(startMode),'Modo de inicio inválido.');
  function dateField(field,action){try{return action();}catch(e){e.field=field;throw e;}}
  const startDate=startMode==='now'?now:dateField('start',()=>{const t=wallToInstant(input.start,profile.zone);requireThat(t>now,'El inicio programado debe estar en el futuro. Elegí AHORA si la calle ya está cortada.');return t;});
  const endDate=dateField('end',()=>{if(input.durationMinutes!==undefined){requireThat(Number.isInteger(input.durationMinutes)&&input.durationMinutes>0&&input.durationMinutes<=43200,'Elegí una duración válida (máximo 30 días).');return startDate+input.durationMinutes*60000;}const t=wallToInstant(input.end,profile.zone);requireThat(t>startDate,startMode==='now'?'El final debe ser posterior a AHORA. Ajustá la hora de finalización.':'El final debe ser posterior al inicio.');return t;});
  requireThat(endDate-startDate<=30*86400000,'En esta versión, cada operativo admite hasta 30 días.');
  requireThat(startDate-now<=366*86400000,'En esta versión, programá dentro de los próximos 366 días.');
  requireThat(Array.isArray(input.rows),'Leé primero los tramos.');
  const selected=sdk.Editing.getSelection();
  requireThat(selected?.objectType==='segment'&&Array.isArray(selected.ids)&&input.rows.every(r=>selected.ids.includes(r.id)),'Cambió la selección del mapa. Volvé a leer los tramos.');
  const segments=snapshotSegments(sdk,input.rows.map(r=>r.id));
  const operations=[];
  segments.forEach((s,i)=>{
    const choice=input.rows[i].choice;
    requireThat(['allowed','forward','reverse'].includes(choice),'Selección de sentido inválida.');
    const directions=choice==='allowed'?s.directions:[choice==='forward'];
    requireThat(directions.every(d=>s.directions.includes(d)),`Sentido contrario a la circulación en el tramo ${s.id}.`);
    for(const isForward of directions)operations.push({segmentId:s.id,isForward,description,startDate,endDate,
      fromNodeClosed:false,isPermanent:false,trafficEventId:null});
  });
  checkConflicts(sdk,operations,profile.zone);
  const plan={version:VERSION,profile,region:sdk.Settings.getRegionCode(),segments,operations,createdAt:now,
    input:JSON.parse(JSON.stringify(input))};
  requireThat(typeof plan.region==='string'&&plan.region.length>0,'No se pudo determinar la región de Waze.');
  function freeze(v){if(v&&typeof v==='object'){Object.values(v).forEach(freeze);Object.freeze(v);}return v;}
  return freeze(plan);
}
// Only adds to WME's editable model. NEVER calls Editing.save or undo/undoAll.
const consumedPlans=new WeakSet();
function stagePlan(sdk,plan,confirmed,now=Date.now()){
  requireThat(confirmed===true,'Confirmá el resumen antes de incorporar al editor.');
  requireThat(plan&&typeof plan==='object'&&!consumedPlans.has(plan),'Este lote ya fue utilizado.');
  requireThat(now>=plan.createdAt && now-plan.createdAt<=120000,'El resumen venció. Preparalo de nuevo.');
  const fresh=makePlan(sdk,plan.input,now);
  // AHORA is a reviewed intent. A chosen duration moves with its start;
  // an explicit end date remains fixed. All safety checks run again.
  const expectedOperations=plan.input.startMode==='now'?plan.operations.map(o=>({...o,startDate:now,endDate:plan.input.durationMinutes===undefined?o.endDate:now+plan.input.durationMinutes*60000})):plan.operations;
  requireThat(fresh.region===plan.region&&JSON.stringify(fresh.segments)===JSON.stringify(plan.segments)&&JSON.stringify(fresh.operations)===JSON.stringify(expectedOperations),'Los tramos o permisos cambiaron. Prepará otro resumen.');
  consumedPlans.add(plan);let added=0;
  try{for(const operation of fresh.operations){sdk.DataModel.RoadClosures.addClosure({...operation});added++;}}
  catch(error){throw Error(`Lote interrumpido tras ${added} alta(s) confirmada(s) por el SDK. Puede haber cambios parciales. NO guardes sin revisar; usá Deshacer de Waze si corresponde. Detalle: ${error.message}`);}
  return {added,status:'pending-wme-save',startDate:fresh.operations[0].startDate};
}
function templateOf(name,profile,region,rows){
  validIds(rows.map(r=>r.id));requireThat(typeof region==='string'&&region.length>0&&region.length<16,'Región inválida.');
  return {name:label(name,'Plantilla',60),profile:profileOf(profile),region,rows:rows.map(r=>{requireThat(['allowed','forward','reverse'].includes(r.choice),'Sentido inválido.');return {id:r.id,choice:r.choice};})};
}
// Explicit file export only: no network access, credentials or Waze mutations.
function exportForPanel(sdk,rows,profile){
  ensureEditorClean(sdk);
  const selected=sdk.Editing.getSelection();
  requireThat(selected?.objectType==='segment'&&rows.every(r=>selected.ids.includes(r.id)),'Volvé a seleccionar las calles antes de exportar.');
  const segments=snapshotSegments(sdk,rows.map(r=>r.id)).map((s,i)=>{
    requireThat(s.street!=='(sin nombre)'&&s.coordinates.length<=100,'La calle debe tener nombre y hasta 100 puntos. Revisá el tramo '+s.id+'.');
    const choice=rows[i].choice;
    requireThat(['allowed','forward','reverse'].includes(choice),'Sentido inválido.');
    const directions=choice==='allowed'?s.directions:[choice==='forward'];
    requireThat(directions.every(d=>s.directions.includes(d)),'Sentido contrario a la circulación.');
    const reverse=directions.length===1&&!directions[0];
    const coordinates=reverse?[...s.coordinates].reverse():s.coordinates;
    return {wazeSegmentId:String(s.id),street:s.street,from:'Nodo Waze '+(reverse?s.toNodeId:s.fromNodeId),to:'Nodo Waze '+(reverse?s.fromNodeId:s.toNodeId),direction:directions.length===2?'BOTH_DIRECTIONS':'ONE_DIRECTION',points:coordinates.map(([lon,lat])=>[lat,lon])};
  });
  return {format:'municipal-closures-segments',schema:1,profile:profileOf(profile),region:sdk.Settings.getRegionCode(),segments};
}


// In-memory schematic from the loaded geometries, never a replacement basemap.
function drawClosureMap(segments,{selected=segments.map(s=>s.id),operations=[],onToggle,compact=false}={}){
  const ns='http://www.w3.org/2000/svg';
  const svg=(name,attrs={})=>{const n=document.createElementNS(ns,name);for(const [k,v] of Object.entries(attrs))n.setAttribute(k,String(v));return n;};
  const width=compact?400:600,height=compact?280:360;
  const map=svg('svg',{viewBox:`0 0 ${width} ${height}`,class:'mc-map',role:onToggle?'group':'img','aria-label':onToggle?'Mapa de calles ficticias: pulsá para seleccionar':'Esquema de los tramos del cierre. Las flechas indican el sentido.'});
  map.append(svg('rect',{width,height,rx:18,fill:'#edf2ef'}));
  if(!segments.length)return map;
  const coords=segments.flatMap(s=>s.coordinates),lat=coords.reduce((n,p)=>n+p[1],0)/coords.length,cos=Math.max(.05,Math.cos(lat*Math.PI/180));
  const xs=coords.map(p=>p[0]*cos),ys=coords.map(p=>p[1]);const minX=Math.min(...xs),maxX=Math.max(...xs),minY=Math.min(...ys),maxY=Math.max(...ys),scale=Math.min((width-100)/Math.max(maxX-minX,.00001),(height-100)/Math.max(maxY-minY,.00001));
  const project=p=>[width/2+(p[0]*cos-(minX+maxX)/2)*scale,height/2-(p[1]-(minY+maxY)/2)*scale];
  for(const [i,s] of segments.entries()){
    const chosen=selected.includes(s.id),pts=s.coordinates.map(project),points=pts.map(p=>p.join(',')).join(' '),g=svg('g',onToggle?{role:'button',tabindex:0,'aria-label':s.street,'aria-pressed':chosen,class:'mc-road'}:{});
    g.append(svg('polyline',{points,fill:'none',stroke:'#fff','stroke-width':26,'stroke-linecap':'round','stroke-linejoin':'round'}));
    g.append(svg('polyline',{points,fill:'none',stroke:chosen?'#11756b':'#9bada9','stroke-width':12,'stroke-linecap':'round','stroke-linejoin':'round'}));
    const mid=Math.floor((pts.length-1)/2),a=pts[mid],b=pts[mid+1],cx=(a[0]+b[0])/2,cy=(a[1]+b[1])/2,angle=Math.atan2(b[1]-a[1],b[0]-a[0])*180/Math.PI;
    const directions=operations.length?operations.filter(o=>o.segmentId===s.id).map(o=>o.isForward):s.directions;
    for(const [n,fwd] of directions.entries()){const offset=directions.length===2?(n===0?-16:16):0;g.append(svg('path',{d:'M -5 -5 L 2 0 L -5 5',fill:'none',stroke:chosen?'#fff':'#29413c','stroke-width':2.5,transform:`translate(${cx+offset*Math.cos(angle*Math.PI/180)},${cy+offset*Math.sin(angle*Math.PI/180)}) rotate(${angle+(fwd?0:180)})`,'pointer-events':'none'}));}
    g.append(svg('circle',{cx:a[0],cy:a[1],r:13,fill:chosen?'#11756b':'#607671',stroke:'#fff','stroke-width':2}));
    const number=svg('text',{x:a[0],y:a[1]+5,'text-anchor':'middle',fill:'#fff','font-size':compact?16:12,'font-family':'system-ui','font-weight':700});number.textContent=String(i+1);g.append(number);
    if(onToggle){const toggle=()=>onToggle(s.id);g.addEventListener('click',toggle);g.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();toggle();}});}
    map.append(g);
  }
  const north=svg('text',{x:width-35,y:30,'font-size':14,fill:'#4d6960'});north.textContent='N ↑';map.append(north);return map;
}

function mountPanel(root,sdk,{storage,simulation=false,compact=!simulation,onSimulationApprove}={}){
  const KEY='municipal-closures.v1';let segments=[],rows=[],plan=null,pending=false,templates=[],duration=60,step=1;
  const el=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n;};
  const button=(text,fn,primary=false)=>{const b=el('button',text);b.type='button';b.onclick=fn;if(primary)b.className='mc-primary';return b;};
  root.classList.add('mc-simple');
  root.append(el('style',`.mc-simple{font:14px/1.5 system-ui,sans-serif;color:#193c37;padding:18px;max-width:780px}.mc-simple *{box-sizing:border-box}.mc-simple [hidden]{display:none!important}.mc-simple h2{font-size:25px;line-height:1.2;margin:8px 0}.mc-simple h3{font-size:19px;margin:0 0 16px}.mc-simple p{margin:10px 0}.mc-simple button{font:inherit;border:1px solid #b9cdc6;border-radius:9px;padding:10px 14px;background:#fff;color:#23473e;cursor:pointer}.mc-simple button:hover{background:#eff7f2}.mc-simple button:disabled{opacity:.45;cursor:not-allowed}.mc-simple .mc-primary{background:#116e60;color:#fff;border-color:#116e60;width:100%;font-weight:650;padding:13px;margin-top:15px}.mc-simple .mc-primary:hover{background:#0c574c}.mc-simple section{background:#fff;border:1px solid #d2e1da;border-radius:14px;padding:20px;margin-top:16px}.mc-simple label{display:block;font-weight:600;margin:12px 0}.mc-simple input,.mc-simple select{display:block;width:100%;font:inherit;padding:10px;border:1px solid #b3c7bf;border-radius:8px;margin-top:6px;background:#fff;color:#183e33}.mc-simple .mc-muted{font-size:12px;color:#60776e}.mc-simple .mc-chips{display:flex;flex-wrap:wrap;gap:7px;margin:14px 0}.mc-simple .mc-chips button[aria-pressed=true]{background:#e0f5ea;border:2px solid #11756b;color:#0b5548}.mc-simple .mc-steps{display:flex;gap:8px;font-size:12px;color:#789086;margin:16px 0}.mc-simple .mc-steps span{padding:6px 10px;border-radius:18px;background:#edf3ef}.mc-simple .mc-steps [aria-current=step]{background:#d8f2e6;color:#085442;font-weight:700}.mc-simple .mc-street{display:flex;align-items:center;gap:8px;padding:9px 0;border-bottom:1px solid #edf2ef}.mc-simple .mc-street span{flex:1}.mc-simple .mc-street button{padding:4px 9px}.mc-simple .mc-note{background:#fdf3d8;padding:12px;border-radius:9px;font-size:12px}.mc-simple [role=alert]:not(:empty){background:#fff0ec;border-left:4px solid #ba422b;padding:12px;margin-top:12px}.mc-simple [aria-invalid=true]{border:2px solid #ba422b}.mc-simple .mc-map{width:100%;display:block;margin:14px 0;border-radius:12px}.mc-simple .mc-receipt{font-size:19px;line-height:1.5}.mc-simple details{margin-top:20px;font-size:12px}.mc-simple summary{cursor:pointer;color:#567367}.mc-simple .mc-actions{display:flex;gap:8px;margin-top:14px}.mc-simple .mc-success{background:#e5f5ed;border-color:#8fbdab}.mc-simple .mc-back{font-size:12px;border:0;padding:5px 0;background:transparent}.mc-simple ol{padding-left:24px}.mc-simple li{padding:4px 0}`));
  const cityTitle=el('p');cityTitle.className='mc-muted';root.append(cityTitle,el('h2','Cerrar calles'));
  root.classList.toggle('mc-compact',compact);
  root.append(el('style',`
    /* WME gives ordinary buttons a fixed 32px height. Reset only our controls. */
    .mc-simple{width:100%;min-width:0;text-align:left;overflow-wrap:break-word}
    .mc-simple button,.mc-simple input,.mc-simple select{height:auto;min-height:40px;max-height:none;min-width:0;max-width:100%;line-height:1.35;white-space:normal;text-transform:none;letter-spacing:normal;box-shadow:none}
    .mc-simple button{display:inline-flex;align-items:center;justify-content:center;vertical-align:middle;text-align:center}
    .mc-simple button:focus-visible,.mc-simple summary:focus-visible{outline:2px solid #11756b;outline-offset:2px}
    .mc-simple .mc-street span{min-width:0;overflow-wrap:anywhere}.mc-simple .mc-street button{flex-shrink:0}
    .mc-simple .mc-steps{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:4px}
    .mc-simple .mc-steps span{padding:6px 3px;text-align:center;line-height:1.3}
    .mc-simple .mc-back{margin-bottom:8px;min-height:32px}
    .mc-simple .mc-caption{font-size:11px;color:#60776e}
    .mc-simple.mc-compact{padding:8px;font-size:13px}
    .mc-simple.mc-compact h2{font-size:21px;margin:6px 0}
    .mc-simple.mc-compact h3{font-size:17px;line-height:1.3;margin:0 0 10px;font-weight:650}
    .mc-simple.mc-compact section{padding:10px;margin-top:10px;border-radius:10px}
    .mc-simple.mc-compact p{margin:8px 0}
    .mc-simple.mc-compact .mc-steps{font-size:11px;margin:10px 0}
    .mc-simple.mc-compact .mc-receipt{font-size:17px;line-height:1.35}
    .mc-simple.mc-compact .mc-primary{padding:10px;margin-top:10px;min-height:42px}
    .mc-simple.mc-compact .mc-chips{display:grid;grid-template-columns:1fr 1fr;gap:6px;margin:10px 0}
    .mc-simple.mc-compact .mc-chips button{padding:8px 5px}
    .mc-simple.mc-compact .mc-note{padding:9px;font-size:11px;line-height:1.45}
    .mc-simple.mc-compact .mc-map{margin:8px 0}
    .mc-simple.mc-compact ol{padding-left:20px;margin:8px 0}
    .mc-simple.mc-compact li{font-size:12px;padding:3px 0}
    .mc-simple.mc-compact details{margin-top:12px}
    .mc-simple .mc-preview-dialog{position:fixed;inset:0;width:min(680px,calc(100vw - 32px));max-width:calc(100vw - 32px);height:auto;max-height:calc(100dvh - 32px);margin:auto;padding:20px;border:1px solid #b3c7bf;border-radius:14px;background:#fff;color:#193c37;overflow:auto;font:14px/1.5 system-ui;box-shadow:0 15px 60px #0004}
    .mc-simple .mc-preview-dialog:not([open]){display:none}
    .mc-simple .mc-preview-dialog[open]{display:block}
    .mc-simple .mc-preview-dialog::backdrop{background:#102a3566}
    .mc-simple .mc-preview-dialog .mc-map{max-height:55dvh}
  `));
  const subtitle=el('p',simulation?'DEMO · Probá con calles ficticias. No publica en Waze.':'Seleccioná los tramos en el mapa de Waze.');subtitle.className='mc-muted';root.append(subtitle);
  const steps=el('nav');steps.className='mc-steps';const stepLabels=['1 · Calles','2 · Horario','3 · Revisar'].map(t=>el('span',t));steps.append(...stepLabels);root.append(steps);
  const first=el('section'),second=el('section'),third=el('section'),result=el('section');root.append(first,second,third,result);
  const error=el('p');error.setAttribute('role','alert');error.tabIndex=-1;
  const showStep=n=>{step=n;[first,second,third,result].forEach((s,i)=>{s.hidden=i+1!==n;});stepLabels.forEach((s,i)=>{if(i+1===n)s.setAttribute('aria-current','step');else s.removeAttribute('aria-current');});[first,second,third,result][n-1].append(error);};
  const clearError=()=>{error.textContent='';for(const i of [start,end])i.removeAttribute('aria-invalid');};
  const fail=e=>{error.textContent=e.message;const f={start,end}[e.field];if(f)f.setAttribute('aria-invalid','true');error.scrollIntoView({block:'nearest'});};
  let previewDialog=null;
  const invalidate=()=>{plan=null;if(previewDialog?.open)previewDialog.close();clearError();if(step===3)showStep(2);};
  first.append(el('h3','Elegí las calles'));
  const count=el('p'),streetList=el('div');first.append(count,streetList);
  const next=button('Elegir motivo y horario',()=>{clearError();if(!rows.length)return fail(Error('Primero seleccioná una calle en el mapa.'));showStep(2);},true);first.append(next);
  const refresh=button('Actualizar selección del mapa',()=>readSelection());refresh.className='mc-back';first.append(refresh);
  const backToStreets=button('← Volver a las calles',()=>{invalidate();showStep(1);});backToStreets.className='mc-back';second.append(backToStreets,el('h3','¿Por qué y hasta cuándo?'));
  function select(parent,title,options){const l=el('label',title),s=el('select');s.setAttribute('aria-label',title);for(const [v,t] of options){const o=el('option',t);o.value=v;s.append(o);}l.append(s);parent.append(l);return s;}
  function field(parent,title,type){const l=el('label',title),i=el('input');i.type=type;l.append(i);parent.append(l);return i;}
  const reason=select(second,'Motivo',[['','Elegí un motivo'],['Obras','Obras'],['Inundación','Inundación'],['Accidente','Accidente'],['Evento','Evento'],['Otro','Otro motivo']]);
  const customReason=field(second,'Describí el motivo','text');customReason.maxLength=39;customReason.parentElement.hidden=true;
  const startMode=select(second,'Inicio',[['now','AHORA'],['scheduled','Programar para más tarde']]);
  const start=field(second,'Fecha y hora de inicio','datetime-local');start.parentElement.hidden=true;
  second.append(el('p','¿Cuánto dura?'));const durationButtons=el('div');durationButtons.className='mc-chips';second.append(durationButtons);
  const end=field(second,'Fecha y hora de finalización','datetime-local');end.parentElement.hidden=true;
  const durationChoices=[[30,'30 minutos'],[60,'1 hora'],[120,'2 horas'],[null,'Elegir final']];
  function updateDuration(){for(const [i,b] of [...durationButtons.children].entries())b.setAttribute('aria-pressed',String(duration===durationChoices[i][0]));end.parentElement.hidden=duration!==null;}
  for(const [minutes,title] of durationChoices)durationButtons.append(button(title,()=>{duration=minutes;invalidate();updateDuration();}));updateDuration();
  const timeHint=el('p');timeHint.className='mc-muted';second.append(timeHint);
  second.append(button('Ver resumen del cierre',()=>review(),true));
  const backToSchedule=button('← Cambiar motivo u horario',()=>{invalidate();showStep(2);});backToSchedule.className='mc-back';third.append(backToSchedule,el('h3','Revisá antes de aprobar'));
  const receipt=el('p');receipt.className='mc-receipt';const graphic=el('div'),legend=el('ol'),timing=el('p'),warning=el('p');warning.className='mc-note';
  const caption=el('p','Norte ↑ · Flechas: sentidos del cierre');caption.className='mc-caption';
  previewDialog=el('dialog');previewDialog.className='mc-preview-dialog';previewDialog.setAttribute('aria-label','Esquema ampliado del cierre');root.append(previewDialog);
  const enlarge=button('Ampliar esquema',()=>{if(!plan)return;previewDialog.replaceChildren(el('h3','Esquema del cierre'),el('p',receipt.textContent),drawClosureMap(plan.segments,{operations:plan.operations}),legend.cloneNode(true),el('p','Vista de consulta. No aprueba ni publica el cierre.'),button('Cerrar vista ampliada',()=>previewDialog.close()));previewDialog.showModal();});
  third.append(receipt,timing,graphic,caption,enlarge,legend,warning);
  const approve=button(simulation?'Aprobar simulación':'Aprobar y llevar a Waze',()=>approvePlan(),true);third.append(approve);
  const preferences=el('details');preferences.append(el('summary','Configuración, sentidos y plantillas'));root.append(preferences);
  const city=field(preferences,'Municipio','text'),zone=field(preferences,'Zona horaria','text');city.value='Río Tercero';zone.value='America/Argentina/Cordoba';
  const profile=()=>profileOf({city:city.value,zone:zone.value});
  const directions=el('div');preferences.append(directions);
  const templateName=field(preferences,'Nombre de plantilla','text'),templateList=el('div');templateName.maxLength=60;
  const persist=()=>{if(!storage)throw Error('No está disponible el almacenamiento local.');storage.setItem(KEY,JSON.stringify({schema:1,profile:profile(),templates}));};
  const profileLabel=()=>{cityTitle.textContent=city.value+' · Piloto '+VERSION;timeHint.textContent='Horarios locales de '+city.value+'.';};
  preferences.append(button('Guardar configuración',()=>{try{persist();invalidate();profileLabel();renderTemplates();preferences.open=false;}catch(e){fail(e);}}));
  preferences.append(button('Guardar calles como plantilla',()=>{try{if(templates.length>=30)throw Error('Máximo de 30 plantillas.');const t=templateOf(templateName.value,profile(),sdk.Settings.getRegionCode(),rows);if(templates.some(x=>x.name===t.name&&x.profile.city===t.profile.city&&x.region===t.region))throw Error('Ese nombre ya existe.');const before=templates;templates=[...templates,t];try{persist();}catch(e){templates=before;throw e;}renderTemplates();}catch(e){fail(e);}}),templateList);
  preferences.append(button('Exportar calles para el panel municipal',()=>{try{const data=exportForPanel(sdk,rows,profile());const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));const link=el('a');link.href=url;link.download='calles-municipales.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(e){fail(e);}}));
  const detailNote=el('p','Las plantillas de este complemento son locales. Para compartirlas, exportá las calles e importalas en el panel municipal; allí administración arma las plantillas para los operadores. Exportar no publica cierres ni transmite credenciales.');preferences.append(detailNote);
  function renderTemplates(){templateList.replaceChildren();let p;try{p=profile();}catch{return;}for(const t of templates.filter(t=>t.profile.city===p.city&&t.profile.zone===p.zone&&t.region===sdk.Settings.getRegionCode())){
    templateList.append(button('Usar '+t.name,()=>{try{ensureEditorClean(sdk);sdk.Editing.setSelection({selection:{objectType:'segment',ids:t.rows.map(r=>r.id)}});readSelection();for(const r of t.rows){const s=segments.find(s=>s.id===r.id);if(!s||(r.choice!=='allowed'&&!s.directions.includes(r.choice==='forward')))throw Error('Cambió la circulación de la plantilla. Volvé a elegir calles.');}rows=t.rows.map(r=>({...r}));start.value='';end.value='';startMode.value='now';start.parentElement.hidden=true;duration=60;updateDuration();renderRows();invalidate();showStep(1);preferences.open=false;}catch(e){rows=[];segments=[];renderRows();fail(e);}}));
    templateList.append(button('Eliminar '+t.name,()=>{const before=templates;try{templates=templates.filter(x=>x!==t);persist();renderTemplates();}catch(e){templates=before;fail(e);}}));
  }}
  function renderRows(){count.textContent=rows.length?`${rows.length} tramo${rows.length===1?'':'s'} seleccionado${rows.length===1?'':'s'}`:'Hacé clic en las calles del mapa para empezar.';next.disabled=!rows.length;streetList.replaceChildren();directions.replaceChildren();for(const [i,r] of rows.entries()){
    const s=segments.find(s=>s.id===r.id),line=el('div');line.className='mc-street';line.append(el('span',`${i+1}. ${s.street}`),button('Quitar',()=>{try{sdk.Editing.setSelection({selection:{objectType:'segment',ids:rows.filter(x=>x.id!==r.id).map(x=>x.id)}});readSelection();}catch(e){fail(e);}}));streetList.append(line);
    const d=select(directions,s.street+' — sentido',[['allowed',s.directions.length===2?'Ambos sentidos':'Sentido de circulación'],...s.directions.map(f=>[f?'forward':'reverse',f?'A → B':'B → A'])]);d.value=r.choice;d.onchange=()=>{r.choice=d.value;invalidate();};
  }}
  function readSelection(){if(pending)return;try{const selection=sdk.Editing.getSelection();const old=rows;if(!selection?.ids?.length){segments=[];rows=[];}else{segments=readCurrent(sdk);rows=segments.map(s=>({id:s.id,choice:old.find(r=>r.id===s.id)?.choice||'allowed'}));}invalidate();renderRows();showStep(1);}catch(e){segments=[];rows=[];renderRows();invalidate();showStep(1);fail(e);}}
  function review(){invalidate();try{if(!reason.value)throw Error('Elegí el motivo del cierre.');if(reason.value==='Otro'&&!customReason.value.trim())throw Error('Escribí brevemente el motivo del cierre.');const input={profile:profile(),description:reason.value==='Otro'?customReason.value:reason.value,startMode:startMode.value,start:start.value,end:end.value,rows:rows.map(r=>({...r}))};if(duration!==null)input.durationMinutes=duration;plan=makePlan(sdk,input);
    receipt.textContent=`Cerrar ${plan.segments.length} tramo${plan.segments.length===1?'':'s'} por ${input.description.toLowerCase()}.`;
    const o=plan.operations[0],durationText=duration===60?'1 hora':duration===120?'2 horas':`${duration} minutos`;timing.textContent=(input.startMode==='now'?'Desde AHORA':`Desde ${displayTime(o.startDate,plan.profile.zone)}`)+(duration!==null?` · por ${durationText} · final ${input.startMode==='now'?'estimado ':''}${displayTime(o.endDate,plan.profile.zone)}`:` · hasta ${displayTime(o.endDate,plan.profile.zone)}`)+` · hora de ${plan.profile.city}.`;
    graphic.replaceChildren(drawClosureMap(plan.segments,{operations:plan.operations,compact}));legend.replaceChildren(...plan.segments.map(s=>el('li',`${s.street} · ${s.city} · ${plan.operations.filter(o=>o.segmentId===s.id).length===2?'ambos sentidos':'un sentido (ver flecha)'}`)));
    warning.textContent=simulation?'Al aprobar se completa una simulación. No se envía nada a Waze.':'Al aprobar se prepara el lote en el editor. Después usá Guardar en Waze. Revisá también sus cierres existentes antes de guardar.';
    if(input.startMode==='now')warning.textContent+=' AHORA se actualiza al aprobar; una duración elegida se cuenta desde ese instante.';
    approve.disabled=false;showStep(3);
  }catch(e){showStep(2);fail(e);}}
  function lock(value){pending=value;for(const c of preferences.querySelectorAll('input,select,button'))c.disabled=value;refresh.disabled=value;}
  function approvePlan(){if(!plan||pending)return;approve.disabled=true;const reviewed=plan;try{const outcome=stagePlan(sdk,reviewed,true);plan=null;lock(true);result.replaceChildren(el('h3','Pendiente de guardar en Waze'),el('p',`${outcome.added} cierres direccionales preparados. Revisá el editor y pulsá su botón Guardar.`));
      result.append(button('Volver a empezar',()=>{try{ensureEditorClean(sdk);lock(false);result.classList.remove('mc-success');readSelection();}catch(e){fail(e);}}));showStep(4);
      if(simulation&&onSimulationApprove){onSimulationApprove();result.replaceChildren(el('h3','Simulación aprobada'),el('p','Listo. Completaste el operativo de prueba. No se publicó ningún cierre en Waze.'),el('p',`${reviewed.segments.length} tramos · ${reviewed.input.description}`),button('Preparar otro cierre',()=>{lock(false);result.classList.remove('mc-success');readSelection();}));result.classList.add('mc-success');showStep(4);}
    }catch(e){plan=null;lock(true);result.replaceChildren(el('h3','Revisá el editor antes de continuar'),button('Comprobar y volver',()=>{try{ensureEditorClean(sdk);lock(false);readSelection();}catch(e){fail(e);}}));showStep(4);fail(e);}}
  for(const control of [reason,customReason,startMode,start,end,city,zone]){control.addEventListener('input',invalidate);}
  reason.onchange=()=>{invalidate();customReason.parentElement.hidden=reason.value!=='Otro';};
  startMode.onchange=()=>{invalidate();start.parentElement.hidden=startMode.value==='now';};
  for(const control of [city,zone])control.addEventListener('input',()=>{profileLabel();renderTemplates();});
  sdk.Events.on({eventName:'wme-selection-changed',eventHandler:()=>readSelection()});
  sdk.Events.on({eventName:'wme-logged-out',eventHandler:()=>{plan=null;lock(true);result.replaceChildren(el('h3','Sesión cerrada'),el('p','Iniciá sesión en Waze y recargá el editor.'));showStep(4);}});
  sdk.Events.on({eventName:'wme-save-finished',eventHandler:event=>{if(pending&&!simulation){const p=el('p',event?.success===true?'Waze confirmó el guardado. Verificá los cierres en su panel.':'Waze no confirmó el guardado. Revisá sus errores.');result.append(p);}}});
  try{const raw=storage?.getItem(KEY);if(raw){if(raw.length>100000)throw Error('Perfil local demasiado grande.');const data=JSON.parse(raw);if(data.schema!==1||!Array.isArray(data.templates)||data.templates.length>30)throw Error('Perfil no compatible.');const p=profileOf(data.profile);city.value=p.city;zone.value=p.zone;templates=data.templates.map(t=>templateOf(t.name,t.profile,t.region,t.rows));}}catch(e){showStep(1);fail(e);}
  profileLabel();renderTemplates();showStep(1);readSelection();return {invalidate};
}

async function launch(){
  if(window.top!==window || !isWmeLocation(location.origin,location.pathname))return;
  for(let n=0;!window.SDK_INITIALIZED&&n<60;n++)await new Promise(r=>setTimeout(r,500));
  if(!window.SDK_INITIALIZED)throw Error('SDK de Waze no disponible. Recargá el editor.');
  await window.SDK_INITIALIZED;
  const sdk=window.getWmeSdk({scriptId:'municipal-closures-v1',scriptName:'Cierres municipales'});
  const {tabLabel,tabPane}=await sdk.Sidebar.registerScriptTab();tabLabel.textContent='Cierres municipales';
  let storage;try{storage=window.localStorage;}catch{}
  mountPanel(tabPane,sdk,{storage});
}
launch().catch(e=>console.error('Cierres municipales:',e.message));

})();
