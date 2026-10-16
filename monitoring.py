"""Read-only Waze observations and separate, audited municipal case management."""
import json
import math
import re
import time
from pathlib import Path

STATUSES = ('pending', 'verified', 'assigned', 'progress', 'resolved')
AREAS = ('', 'Defensa Civil', 'Obras Públicas', 'Tránsito', 'Otra área')
RETENTION = 30 * 86400
MAX_RECORDS = 20000


def number(value, minimum, maximum):
    if type(value) not in (int, float) or not math.isfinite(value) or not minimum <= value <= maximum:
        return None
    return value


def label(value, limit=200):
    return value[:limit] if isinstance(value, str) else ''


def normalize(item, bounds, now):
    if not isinstance(item, dict):
        raise ValueError('Invalid alert')
    uid = item.get('uuid')
    if not isinstance(uid, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', uid):
        raise ValueError('Invalid ID')
    loc = item.get('location')
    if not isinstance(loc, dict):
        raise ValueError('Invalid location')
    lat, lon = number(loc.get('y'), -90, 90), number(loc.get('x'), -180, 180)
    stamp = number(item.get('pubMillis'), 1, (now+300)*1000)
    if lat is None or lon is None or stamp is None or not isinstance(item.get('type'), str):
        raise ValueError('Invalid alert fields')
    s, w, n, e = bounds
    if not s <= lat <= n or not w <= lon <= e:
        return None
    return {'id':uid, 'type':label(item.get('type'),40), 'subtype':label(item.get('subtype'),80),
            'street':label(item.get('street')), 'city':label(item.get('city')), 'point':[lat,lon],
            'publishedAt':stamp/1000, 'description':label(item.get('reportDescription'),1000),
            'reliability':number(item.get('reliability'),0,10), 'confidence':number(item.get('confidence'),-1,10),
            'thumbsUp':number(item.get('nThumbsUp'),0,1000000),
            'municipal':item.get('reportByMunicipalityUser') in (True,'true'),
            'heading':number(item.get('magvar'),0,359)}


def merge(previous, payload, bounds, now=None):
    now = time.time() if now is None else now
    if not isinstance(payload, dict) or not any(k in payload for k in ('alerts','startTimeMillis','endTimeMillis')):
        raise ValueError('Invalid feed')
    raw = payload.get('alerts', [])
    source = number(payload.get('endTimeMillis'), 1, (now+300)*1000)
    if not isinstance(raw, list) or len(raw)>5000 or source is None:
        raise ValueError('Invalid feed envelope')
    source /= 1000
    if source < previous.get('meta',{}).get('sourceTime',0):
        raise ValueError('Older snapshot')
    if now-source > 600:
        raise ValueError('Stale source')
    records = {a['id']:{**a,'present':False} for a in previous.get('alerts',[]) if now-a['lastSeen']<=RETENTION}
    invalid, outside = 0, 0
    seen = set()
    for item in raw:
        try:
            alert = normalize(item,bounds,now)
        except ValueError:
            invalid += 1
            continue
        if alert is None:
            outside += 1
            continue
        if alert['id'] in seen:
            invalid += 1
            continue
        seen.add(alert['id'])
        old = records.get(alert['id'],{})
        records[alert['id']] = {**alert,'firstSeen':old.get('firstSeen',now),'lastSeen':now,'present':True}
    # Invalid records must not make previously observed incidents disappear.
    if invalid:
        for a in previous.get('alerts',[]):
            if a['id'] not in seen and a['id'] in records:
                records[a['id']]['present'] = a.get('present',False)
    alerts = sorted(records.values(),key=lambda a:a['lastSeen'],reverse=True)
    return {'schema':1,'alerts':alerts[:MAX_RECORDS], 'meta':{'lastAttempt':now,'lastSuccess':now,
            'sourceTime':source,'error':None,'invalidCount':invalid,'outsideCount':outside,
            'truncated':len(alerts)>MAX_RECORDS}}


def read_cache(path):
    try:
        with Path(path).open('rb') as stream:
            raw=stream.read(32*1024*1024+1)
        if len(raw)>32*1024*1024:
            raise ValueError('Oversized cache')
        data=json.loads(raw)
        if data.get('schema')!=1 or not isinstance(data.get('alerts'),list) or not isinstance(data.get('meta'),dict):
            raise ValueError('Invalid cache')
        return data
    except (OSError,ValueError,TypeError,AttributeError):
        return {'schema':1,'alerts':[],'meta':{'error':'unavailable'}}


def initialize(db):
    db.execute('CREATE TABLE IF NOT EXISTS monitor_cases (id TEXT PRIMARY KEY, body TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS monitor_audit (id INTEGER PRIMARY KEY AUTOINCREMENT, alert_id TEXT NOT NULL, at REAL NOT NULL, actor TEXT NOT NULL, status TEXT NOT NULL, area TEXT NOT NULL, note TEXT NOT NULL, version INTEGER NOT NULL)')
    db.execute('CREATE INDEX IF NOT EXISTS monitor_audit_alert ON monitor_audit(alert_id,id)')


def listing(db, cache, now=None):
    now=time.time() if now is None else now
    rows={a['id']:dict(a) for a in cache['alerts']}
    for row in db.execute('SELECT id,body FROM monitor_cases'):
        case=json.loads(row['body'])
        if row['id'] not in rows:
            rows[row['id']]={**case['snapshot'],'present':False}
        rows[row['id']]['case']={k:v for k,v in case.items() if k!='snapshot'}
    alerts=sorted(rows.values(),key=lambda a:(a.get('present',False),a['lastSeen']),reverse=True)
    for a in alerts:
        a.setdefault('case',{'status':'pending','area':'','note':'','version':0,'actor':None,'updatedAt':None})
    meta=cache['meta']
    return {'alerts':alerts[:5000],'truncated':len(alerts)>5000,'total':len(alerts),'meta':{**meta,'stale':bool(meta.get('error')) or now-meta.get('lastSuccess',0)>360 or now-meta.get('sourceTime',0)>600},'serverTime':now}


def history(db, uid):
    return [dict(r) for r in db.execute('SELECT at,actor,status,area,note,version FROM monitor_audit WHERE alert_id=? ORDER BY id DESC LIMIT 30',(uid,))]


def update(db, cache, body, actor):
    uid=body.get('id')
    if not isinstance(uid,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',uid):
        raise ValueError('Identificador de alerta inválido.')
    row=db.execute('SELECT body FROM monitor_cases WHERE id=?',(uid,)).fetchone()
    old=json.loads(row['body']) if row else None
    alert=next((a for a in cache['alerts'] if a['id']==uid),None)
    if alert is None and old:
        alert=old['snapshot']
    if alert is None:
        raise ValueError('La alerta no está disponible. Actualizá Monitoreo.')
    version=old['version'] if old else 0
    if type(body.get('version')) is not int or body['version']!=version:
        raise ValueError('Otra persona actualizó este aviso. Cerrá la ficha y volvé a abrirla.')
    status, area, note=body.get('status'),body.get('area'),body.get('note')
    if status not in STATUSES or area not in AREAS or not isinstance(note,str) or len(note)>1000:
        raise ValueError('Estado, área o nota inválidos (máximo 1000 caracteres).')
    if status=='assigned' and not area:
        raise ValueError('Elegí el área a la que se deriva el aviso.')
    if status=='resolved' and not note.strip():
        raise ValueError('Anotá cómo se verificó la resolución antes de marcarlo resuelto.')
    stamp=time.time()
    case={'status':status,'area':area,'note':note.strip(),'version':version+1,'actor':actor,'updatedAt':stamp,'snapshot':alert}
    db.execute('INSERT OR REPLACE INTO monitor_cases(id,body) VALUES(?,?)',(uid,json.dumps(case)))
    db.execute('INSERT INTO monitor_audit(alert_id,at,actor,status,area,note,version) VALUES(?,?,?,?,?,?,?)',
               (uid,stamp,actor,status,area,note.strip(),version+1))
    return case
