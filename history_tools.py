"""Shared research tools. All writes use the operational DB, never the archive."""
from collections import Counter, defaultdict
from contextlib import closing
import math
import re
import time

from catalog import CITY
from history_dashboard import distance, LABELS
from observation_store import connect_readonly


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS history_zones (
        id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE,
        lat REAL NOT NULL, lon REAL NOT NULL, radius INTEGER NOT NULL,
        actor TEXT NOT NULL, at REAL NOT NULL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS history_links (
        id INTEGER PRIMARY KEY, first_id TEXT NOT NULL, second_id TEXT NOT NULL,
        decision TEXT NOT NULL, actor TEXT NOT NULL, at REAL NOT NULL)''')
    db.execute('CREATE INDEX IF NOT EXISTS history_links_pair ON history_links(first_id,second_id,id)')


def zones(db):
    return [dict(r) for r in db.execute('SELECT * FROM history_zones ORDER BY name')]


def resolve_zone(db,f):
    if f['zoneId']:
        row=db.execute('SELECT * FROM history_zones WHERE id=?',(f['zoneId'],)).fetchone()
        if row is None:raise ValueError('La zona guardada no existe.')
        f['zone']=dict(row)


def save_zone(db,body,actor):
    name=body.get('name');lat=body.get('lat');lon=body.get('lon');radius=body.get('radius')
    if not isinstance(name,str) or not 1<=len(name.strip())<=80:
        raise ValueError('Escribí un nombre de zona de hasta 80 caracteres.')
    s,w,n,e=CITY['bounds']
    if any(type(x) not in (int,float) or not math.isfinite(x) for x in (lat,lon)) or not s<=lat<=n or not w<=lon<=e or type(radius) is not int or not 100<=radius<=3000:
        raise ValueError('Marcá un centro dentro del área de Río Tercero y un radio de 100 a 3000 metros.')
    if db.execute('SELECT 1 FROM history_zones WHERE name=? COLLATE NOCASE',(name.strip(),)).fetchone():
        raise ValueError('Ya existe una zona con ese nombre.')
    if db.execute('SELECT COUNT(*) FROM history_zones').fetchone()[0]>=100:
        raise ValueError('Se alcanzó el límite de 100 zonas compartidas.')
    cur=db.execute('INSERT INTO history_zones(name,lat,lon,radius,actor,at) VALUES(?,?,?,?,?,?)',(name.strip(),lat,lon,radius,actor,time.time()))
    return {'id':cur.lastrowid,'zones':zones(db)}


def save_link(db,path,body,actor):
    ids=body.get('ids')
    if not isinstance(ids,list) or len(ids)!=2 or any(not isinstance(x,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',x) for x in ids) or ids[0]==ids[1]:
        raise ValueError('Elegí dos reportes distintos.')
    first,second=sorted(ids);decision=body.get('decision')
    if decision not in ('related','separate','pending'):raise ValueError('Decisión inválida.')
    with closing(connect_readonly(path)) as archive:
        if archive.execute('SELECT COUNT(*) FROM reports WHERE id IN (?,?)',(first,second)).fetchone()[0]!=2:
            raise ValueError('Uno de los reportes no está en el archivo.')
    row=db.execute('SELECT id FROM history_links WHERE first_id=? AND second_id=? ORDER BY id DESC LIMIT 1',(first,second)).fetchone()
    revision=row['id'] if row else 0
    if type(body.get('revision')) is not int or body['revision']!=revision:
        raise ValueError('Otra persona revisó este par. Actualizá la consulta antes de decidir.')
    cur=db.execute('INSERT INTO history_links(first_id,second_id,decision,actor,at) VALUES(?,?,?,?,?)',(first,second,decision,actor,time.time()))
    return dict(db.execute('SELECT * FROM history_links WHERE id=?',(cur.lastrowid,)).fetchone())


def enrich(db,rows):
    by_id={r['id']:r for r in rows}
    states={r['report_id']:r['status'] for r in db.execute('''SELECT n.report_id,n.status FROM history_notes n
        JOIN (SELECT report_id,MAX(id) AS id FROM history_notes GROUP BY report_id) latest ON latest.id=n.id''') if r['report_id'] in by_id}
    counts=Counter(states.get(r['id'],'untouched') for r in rows)
    final={'confirmed','unverified','discarded'}
    reviewed=sum(counts[k] for k in final)
    breakdown=[]
    for key,label in LABELS.items():
        subset=[r for r in rows if r['category']==key]
        if subset:breakdown.append({'label':label,'total':len(subset),**{s:sum(states.get(r['id'])==s for r in subset) for s in final}})
    bands=[]
    for label,test in [('Sin puntuación',lambda x:x is None),('0–4',lambda x:x is not None and x<5),('5–6',lambda x:x is not None and 5<=x<7),('7–10',lambda x:x is not None and x>=7)]:
        subset=[r for r in rows if test(r.get('reliability'))]
        bands.append({'label':label,'total':len(subset),**{s:sum(states.get(r['id'])==s for r in subset) for s in final}})
    decisions={}
    for r in db.execute('''SELECT l.* FROM history_links l JOIN
            (SELECT first_id,second_id,MAX(id) AS id FROM history_links GROUP BY first_id,second_id) latest ON latest.id=l.id'''):
        if r['first_id'] in by_id and r['second_id'] in by_id:decisions[(r['first_id'],r['second_id'])]=dict(r)
    # Spatial index avoids all-pairs comparisons. Cap suggestions independently of totals.
    cells=defaultdict(list); pairs=set(decisions);truncated=len(pairs)>200
    lat_step=75/111320;lon_step=75/(111320*math.cos(math.radians(CITY['center'][0])))
    for r in sorted(rows,key=lambda r:r['id']):
        lat,lon=r['point'];cell=(math.floor(lat/lat_step),math.floor(lon/lon_step))
        if len(pairs)<200:
            for dx in (-1,0,1):
                for dy in (-1,0,1):
                    for other in cells[(cell[0]+dx,cell[1]+dy)]:
                        window=3600 if r['category']=='accident' else 86400
                        if r['type']==other['type'] and r.get('subtype')==other.get('subtype') and abs(r['publishedAt']-other['publishedAt'])<=window and max(r['periodFirst'],other['periodFirst'])<=min(r['periodLast'],other['periodLast']) and distance(r['point'],other['point'])<=75:
                            pairs.add(tuple(sorted((r['id'],other['id']))))
                            if len(pairs)>=200:truncated=True;break
                    if len(pairs)>=200:break
                if len(pairs)>=200:break
        else:truncated=True
        cells[cell].append(r)
    result=[]
    ordered=sorted(pairs,key=lambda p:(p not in decisions,p))[:200]
    for first,second in ordered:
        decision=decisions.get((first,second),{})
        result.append({'ids':[first,second],'reports':[{'id':by_id[i]['id'],'street':by_id[i]['street'],'publishedAt':by_id[i]['publishedAt']} for i in (first,second)],
                       'distance':round(distance(by_id[first]['point'],by_id[second]['point'])),
                       'decision':decision.get('decision','pending'),'revision':decision.get('id',0),'actor':decision.get('actor'),'at':decision.get('at')})
    return {'verification':{'total':len(rows),'reviewed':reviewed,'counts':dict(counts),
                            'confirmedPercent':round(counts['confirmed']*100/reviewed,1) if reviewed else None,'types':breakdown,'bands':bands},
            'related':{'pairs':result,'truncated':truncated}}
