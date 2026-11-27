(()=>{'use strict';
const $=id=>document.getElementById(id),base=document.body.dataset.base;
const num=v=>Number.isFinite(v)?v.toLocaleString('es-AR',{maximumFractionDigits:1}):'—';
const kmh=v=>v==null?'Sin dato':`${num(v)} km/h`,distance=v=>v==null?'—':`${num(v/1000)} km`;
const stamp=v=>v?new Date(v*1000).toLocaleString('es-AR',{timeZone:'America/Argentina/Cordoba',dateStyle:'short',timeStyle:'short'}):'Sin lectura';
const day=()=>new Intl.DateTimeFormat('sv-SE',{timeZone:'America/Argentina/Cordoba',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
const duration=v=>v==null?'Sin dato':`${Math.floor(v/60)} min ${Math.round(v%60)} s`;
const levels=['Fluido','Leve','Moderado','Intenso','Detenido','Cerrado'];
let map,layer,requestId=0,loaded=false,lastQuery=null;
function node(tag,text,cls){const e=document.createElement(tag);if(text!=null)e.textContent=text;if(cls)e.className=cls;return e;}
function cell(tr,text){tr.append(node('td',text));}
function error(text){$('traffic-error').textContent=text;$('traffic-error').hidden=!text;}
function query(){return new URLSearchParams({from:$('traffic-from').value,to:$('traffic-to').value,route:$('traffic-route').value});}
function chart(d){const target=$('traffic-chart');target.replaceChildren();
 if(!d.series.length){target.append(node('p','No hay lecturas de este recorrido en el período.'));return;}
 const NS='http://www.w3.org/2000/svg',svg=document.createElementNS(NS,'svg');svg.setAttribute('viewBox','0 0 1000 260');
 const make=(tag,attrs,text)=>{const e=document.createElementNS(NS,tag);Object.entries(attrs).forEach(([k,v])=>e.setAttribute(k,v));if(text!=null)e.textContent=text;svg.append(e);return e;};
 const values=d.series.flatMap(p=>[p.speed,p.historicSpeed]).filter(Number.isFinite),max=Math.max(20,...values)*1.15;
 const start=Date.parse(d.from+'T00:00:00-03:00')/1000,end=Math.min(Date.parse(d.to+'T23:59:59-03:00')/1000,d.serverTime),span=Math.max(end-start,d.interval);
 for(let i=0;i<=4;i++){const v=max*i/4,y=220-180*i/4;make('line',{x1:55,y1:y,x2:980,y2:y,stroke:'#e0e8e3'});make('text',{x:45,y:y+4,'text-anchor':'end',fill:'#65746c','font-size':12},num(v));}
 for(const p of d.series){const x=55+925*(p.at-start)/span;for(const [key,color] of [['historicSpeed','#aa6d27'],['speed','#087f70']]){if(!Number.isFinite(p[key]))continue;const dot=make('circle',{cx:x,cy:220-180*p[key]/max,r:4,fill:color});const title=document.createElementNS(NS,'title');title.textContent=`${stamp(p.at)} · ${key==='speed'?'Estimada':'Habitual'}: ${kmh(p[key])} · versión ${p.versionId}`;dot.append(title);}}
 make('text',{x:55,y:250,fill:'#65746c','font-size':12},stamp(start));make('text',{x:980,y:250,'text-anchor':'end',fill:'#65746c','font-size':12},stamp(end));make('text',{x:10,y:20,fill:'#65746c','font-size':12},'km/h');target.append(svg);
}
function render(d){$('traffic-results').hidden=false;$('traffic-auth').hidden=true;
 $('traffic-status').textContent=d.stale?'Lectura desactualizada o con falla':'Recolección activa';$('traffic-status').classList.toggle('traffic-warning',d.stale);
 $('traffic-updated').textContent=`Fuente: ${stamp(d.sourceAt)} · Último intento: ${stamp(d.lastAttempt)}. ${d.stale?'Se muestra la última lectura válida; no representa el tránsito actual.':''}`;
 const body=$('traffic-current');body.replaceChildren();
 for(const r of d.current){const tr=node('tr'),td=node('td',null,'traffic-name'),b=node('button',r.name||r.key);b.type='button';b.addEventListener('click',()=>{if(!$('traffic-route').querySelector(`option[value="${CSS.escape(r.key)}"]`))return;$('traffic-route').value=r.key;load(query());$('traffic-filters').scrollIntoView({behavior:'smooth'});});td.append(b,node('small',`${r.fromName||'Inicio'} → ${r.toName||'Fin'} · ${r.key.replace('route:','')}`));tr.append(td);cell(tr,distance(r.length));cell(tr,kmh(r.speed));cell(tr,kmh(r.historicSpeed));cell(tr,duration(r.seconds));cell(tr,r.differenceSeconds==null?'Sin dato':`${r.differenceSeconds>=0?'+':'−'}${duration(Math.abs(r.differenceSeconds))}`);cell(tr,levels[r.jamLevel]||'Sin dato');body.append(tr);}
 if(!d.current.length){const tr=node('tr');const td=node('td','La última lectura no contiene recorridos configurados.');td.colSpan=7;tr.append(td);body.append(tr);}
 const select=$('traffic-route');select.replaceChildren();const routes=new Map(d.catalog.map(r=>[r.key,r]));for(const r of d.current)if(!routes.has(r.key))routes.set(r.key,{...r,active:true});
 for(const r of routes.values()){const o=node('option',`${r.name||r.key} · ${r.key.replace('route:','')}${r.active?'':' · Fuera del seguimiento actual'}`);o.value=r.key;select.append(o);}select.value=d.selectedKey;
 $('traffic-title').textContent=d.selected?.name||'Sin recorrido seleccionado';$('traffic-range').textContent=`${d.from} → ${d.to}`;
 $('traffic-coverage').textContent=`${d.coverage.attempts} intentos de captura · ${d.coverage.failures} fallidos · ${d.coverage.gaps} intervalos sin captura mayores a 6 minutos, incluidos los extremos del período. Los huecos no equivalen a velocidad cero.`;
 if(!map&&window.L){map=L.map('traffic-map').setView([-32.174,-64.115],13);L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:'© OpenStreetMap'}).addTo(map);layer=L.layerGroup().addTo(map);}if(map){layer.clearLayers();if(d.selected?.line?.length){const line=L.polyline(d.selected.line,{color:'#087f70',weight:5}).addTo(layer);map.invalidateSize();map.fitBounds(line.getBounds(),{padding:[25,25],maxZoom:16});}}
 const active=d.current.some(r=>r.key===d.selectedKey);$('traffic-map-note').textContent=active?'Mapa: última versión del recorrido configurado actualmente.':'Mapa: última versión observada en el período. El recorrido está fuera del seguimiento actual.';
 chart(d);const versions=new Set(d.series.map(p=>p.versionId)).size;$('traffic-chart-note').textContent=`Cada punto es el promedio de estimaciones en ${d.interval/3600} hora(s), separado por versión del recorrido (${versions} en este período). No pondera por cantidad de vehículos. Las lecturas repetidas de una misma actualización no se cuentan dos veces; los puntos no se unen a través de huecos.`;
 const series=$('traffic-series');series.replaceChildren();for(const p of d.series){const tr=node('tr');cell(tr,stamp(p.at));cell(tr,`${p.versionId} · ${p.name}`);cell(tr,distance(p.length));cell(tr,kmh(p.speed));cell(tr,kmh(p.historicSpeed));cell(tr,`${num(p.minSpeed)} / ${num(p.maxSpeed)} km/h`);cell(tr,p.samples);series.append(tr);}
 const area=d.area,jams=(area.lengthOfJams||[]).map(r=>`${levels[r.jamLevel]}: ${distance(r.jamLength)}`).join(' · '),users=(area.usersOnJams||[]).map(r=>`${levels[r.jamLevel]}: ${num(r.wazersCount)}`).join(' · ');$('traffic-area').replaceChildren(node('p',`${area.routes??0} recorridos · ${area.subroutes??0} subrecorridos · ${area.irregularities??0} eventos de tránsito inusual.`),node('p',`Longitud congestionada: ${jams||'Sin dato'}.`),node('p',`Indicador de usuarios: ${users||'Sin dato'}.`));
}
async function load(params){const id=++requestId;error('');try{const res=await fetch(`${base}/api/traffic?${params}`,{credentials:'same-origin'});if(id!==requestId)return;if(res.status===401){$('traffic-auth').hidden=false;$('traffic-results').hidden=true;$('traffic-status').textContent='Sesión requerida';return;}const d=await res.json();if(!res.ok)throw Error(d.error||'No se pudieron consultar los recorridos.');if(id!==requestId)return;render(d);loaded=true;lastQuery=new URLSearchParams(params);lastQuery.set('route',d.selectedKey);history.replaceState(null,'',`${base}/traffic?${lastQuery}`);}catch(e){if(id===requestId){error(e.message);$('traffic-status').textContent='No se pudo actualizar';}}}
const initial=new URLSearchParams(location.search);$('traffic-from').value=initial.get('from')||day();$('traffic-to').value=initial.get('to')||day();$('traffic-from').max=day();$('traffic-to').max=day();
$('traffic-filters').addEventListener('submit',e=>{e.preventDefault();load(query());});
load(new URLSearchParams({from:$('traffic-from').value,to:$('traffic-to').value,route:initial.get('route')||''}));setInterval(()=>{if(loaded&&!document.hidden&&lastQuery)load(lastQuery);},120000);
})();
