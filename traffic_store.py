"""Traffic View archive: route estimates, not individual vehicles or road speeds."""
from contextlib import closing
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import time

SCHEMA = '''
CREATE TABLE IF NOT EXISTS traffic_readings (
 id INTEGER PRIMARY KEY, attempted_at REAL NOT NULL, source_at REAL,
 status TEXT NOT NULL, body TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS traffic_readings_time ON traffic_readings(attempted_at);
CREATE INDEX IF NOT EXISTS traffic_readings_source ON traffic_readings(source_at,status);
CREATE TABLE IF NOT EXISTS traffic_versions (
 id INTEGER PRIMARY KEY, fingerprint TEXT UNIQUE NOT NULL, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS traffic_samples (
 reading_id INTEGER NOT NULL REFERENCES traffic_readings(id), route_key TEXT NOT NULL,
 version_id INTEGER NOT NULL REFERENCES traffic_versions(id), kind TEXT NOT NULL,
 length REAL, seconds REAL, historic_seconds REAL, jam_level INTEGER,
 PRIMARY KEY(reading_id,route_key));
CREATE INDEX IF NOT EXISTS traffic_samples_route ON traffic_samples(route_key,reading_id);
'''


def number(value, low=0, high=10000000):
    return value if type(value) in (int, float) and math.isfinite(value) and low <= value <= high else None


def text(value, limit=200):
    return value[:limit] if isinstance(value, str) else ''


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def normalize(payload, now):
    if not isinstance(payload, dict) or not isinstance(payload.get('routes'), list):
        raise ValueError('Invalid Traffic View envelope')
    stamp = number(payload.get('updateTime'), 1, (now+60)*1000)
    if stamp is None or now-stamp/1000 > 600:
        raise ValueError('Invalid Traffic View timestamp')
    rows, seen = [], set()

    def route(raw, kind, parent=''):
        if not isinstance(raw, dict) or len(rows) >= 500:
            raise ValueError('Invalid route')
        points = raw.get('line')
        if not isinstance(points, list) or not 2 <= len(points) <= 10000:
            raise ValueError('Invalid route geometry')
        line = []
        for point in points:
            if not isinstance(point, dict):
                raise ValueError('Invalid point')
            x, y = number(point.get('x'), -180, 180), number(point.get('y'), -90, 90)
            if x is None or y is None:
                raise ValueError('Invalid point')
            line.append([y, x])
        identifier = raw.get('id')
        if kind == 'route':
            if type(identifier) not in (str, int) or not 1 <= len(str(identifier)) <= 128:
                raise ValueError('Invalid route id')
            key = 'route:' + str(identifier)
        else:
            key = kind + ':' + hashlib.sha256(encoded([parent, line]).encode()).hexdigest()[:32]
        if key in seen:
            raise ValueError('Duplicate route')
        seen.add(key)
        body = {'key':key, 'kind':kind, 'parent':parent, 'name':text(raw.get('name')),
                'fromName':text(raw.get('fromName')), 'toName':text(raw.get('toName')), 'line':line,
                'length':number(raw.get('length'),0.01)}
        level = raw.get('jamLevel')
        rows.append({'body':body, 'length':number(raw.get('length'), 0.01),
                     'seconds':number(raw.get('time'), 0.01, 604800),
                     'historicSeconds':number(raw.get('historicTime'), 0.01, 604800),
                     'jamLevel':level if type(level) is int and 0 <= level <= 5 else None})
        children = raw.get('subRoutes', [])
        if not isinstance(children, list):
            raise ValueError('Invalid subroutes')
        if kind == 'route':
            for child in children:
                route(child, 'subroute', key)
    for raw in payload['routes']:
        route(raw, 'route')
    irregularities = payload.get('irregularities', [])
    if not isinstance(irregularities, list):
        raise ValueError('Invalid irregularities')
    for raw in irregularities:
        route(raw, 'irregularity')
    meta = {'usersOnJams':[], 'lengthOfJams':[], 'irregularities':len(irregularities),
            'routes':len(payload['routes']), 'subroutes':sum(r['body']['kind']=='subroute' for r in rows)}
    for key, field in [('usersOnJams','wazersCount'), ('lengthOfJams','jamLength')]:
        values = payload.get(key, [])
        if not isinstance(values, list) or len(values)>10:
            raise ValueError('Invalid area totals')
        for item in values:
            if isinstance(item, dict) and type(item.get('jamLevel')) is int and 0<=item['jamLevel']<=5:
                meta[key].append({'jamLevel':item['jamLevel'],field:number(item.get(field))})
    return stamp/1000, rows, meta


def record(path, payload, now=None, failed=False):
    now = time.time() if now is None else now
    source, rows, meta, status = None, [], {}, 'failed'
    if not failed:
        try:
            source, rows, meta = normalize(payload, now)
            status = 'ok'
        except (ValueError, TypeError, OverflowError):
            pass
    path = Path(path)
    if not path.exists():
        try:
            fd = os.open(path, os.O_CREAT|os.O_EXCL|os.O_WRONLY, 0o640)
            os.close(fd)
        except FileExistsError:
            pass
    with closing(sqlite3.connect(path, timeout=15)) as db:
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA synchronous=FULL')
        db.executescript(SCHEMA)
        with db:
            db.execute('BEGIN IMMEDIATE')
            last = db.execute("SELECT MAX(source_at) FROM traffic_readings WHERE status='ok'").fetchone()[0]
            if status == 'ok' and last is not None and source < last:
                status = 'failed'
            elif status == 'ok' and source == last:
                status = 'repeated'
            reading = db.execute('INSERT INTO traffic_readings(attempted_at,source_at,status,body) VALUES(?,?,?,?)',
                                 (now,source,status,encoded(meta))).lastrowid
            if status == 'ok':
                for row in rows:
                    body = encoded(row['body'])
                    fingerprint = hashlib.sha256(body.encode()).hexdigest()
                    db.execute('INSERT OR IGNORE INTO traffic_versions(fingerprint,body) VALUES(?,?)',(fingerprint,body))
                    version = db.execute('SELECT id FROM traffic_versions WHERE fingerprint=?',(fingerprint,)).fetchone()[0]
                    db.execute('INSERT INTO traffic_samples VALUES(?,?,?,?,?,?,?,?)',
                        (reading,row['body']['key'],version,row['body']['kind'],row['length'],row['seconds'],row['historicSeconds'],row['jamLevel']))
    return status != 'failed'


def speed(length, seconds):
    return round(3.6*length/seconds,1) if length and seconds else None


def sample(row):
    body = json.loads(row['body'])
    length, seconds, historic = row['length'], row['seconds'], row['historic_seconds']
    return {**body, 'length':length, 'seconds':seconds, 'historicSeconds':historic,
            'speed':speed(length,seconds), 'historicSpeed':speed(length,historic), 'jamLevel':row['jam_level'],
            'differenceSeconds':round(seconds-historic,1) if seconds and historic else None}


def dashboard(path, f, key=''):
    if len(key)>160:
        raise ValueError('Recorrido inválido.')
    with closing(sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True,timeout=5)) as db:
        db.row_factory = sqlite3.Row
        deadline = time.monotonic()+12
        db.set_progress_handler(lambda: time.monotonic()>deadline,10000)
        db.execute('BEGIN')
        last = db.execute('SELECT * FROM traffic_readings ORDER BY id DESC LIMIT 1').fetchone()
        good = db.execute("SELECT * FROM traffic_readings WHERE status='ok' ORDER BY id DESC LIMIT 1").fetchone()
        current, meta = [], {}
        if good:
            meta = json.loads(good['body'])
            current = [sample(r) for r in db.execute('SELECT s.*,v.body FROM traffic_samples s JOIN traffic_versions v ON v.id=s.version_id WHERE reading_id=?',(good['id'],))]
        rows = db.execute('''SELECT s.route_key,MAX(s.reading_id) latest,COUNT(*) n,COUNT(DISTINCT s.version_id) versions,
            AVG(CASE WHEN s.seconds>0 THEN 3.6*s.length/s.seconds END) average_speed,
            MIN(CASE WHEN s.seconds>0 THEN 3.6*s.length/s.seconds END) min_speed,
            MAX(CASE WHEN s.seconds>0 THEN 3.6*s.length/s.seconds END) max_speed
            FROM traffic_samples s JOIN traffic_readings r ON r.id=s.reading_id
            WHERE r.source_at>=? AND r.source_at<? AND s.kind='route' GROUP BY s.route_key''',(f['start'],min(f['end'],f['now']+1))).fetchall()
        catalog = []
        for r in rows:
            b = json.loads(db.execute('SELECT v.body FROM traffic_samples s JOIN traffic_versions v ON v.id=s.version_id WHERE s.reading_id=? AND s.route_key=?',(r['latest'],r['route_key'])).fetchone()[0])
            catalog.append({k:v for k,v in b.items() if k!='line'} | {'samples':r['n'],'versions':r['versions'],
                'active':any(c['key']==r['route_key'] for c in current)})
        if not key:
            key = catalog[0]['key'] if catalog else next((r['key'] for r in current if r['kind']=='route'),'')
        if key and key not in {r['key'] for r in catalog} | {r['key'] for r in current if r['kind']=='route'}:
            raise ValueError('El recorrido no tiene lecturas en este período.')
        interval = 3600 if f['days']<=31 else 10800
        series = [dict(r) for r in db.execute('''SELECT CAST((r.source_at-?)/? AS INTEGER)*?+? at,
            s.version_id versionId,AVG(s.length) length,
            AVG(CASE WHEN s.seconds>0 THEN 3.6*s.length/s.seconds END) speed,
            AVG(CASE WHEN s.historic_seconds>0 THEN 3.6*s.length/s.historic_seconds END) historicSpeed,
            AVG(s.seconds) seconds,AVG(s.historic_seconds) historicSeconds,COUNT(*) samples,
            MIN(CASE WHEN s.seconds>0 THEN 3.6*s.length/s.seconds END) minSpeed,
            MAX(CASE WHEN s.seconds>0 THEN 3.6*s.length/s.seconds END) maxSpeed
            FROM traffic_samples s JOIN traffic_readings r ON r.id=s.reading_id
            WHERE s.route_key=? AND r.source_at>=? AND r.source_at<? GROUP BY at,s.version_id ORDER BY at,s.version_id''',
            (f['start'],interval,interval,f['start'],key,f['start'],min(f['end'],f['now']+1)))]
        for point in series:
            version = json.loads(db.execute('SELECT body FROM traffic_versions WHERE id=?',(point['versionId'],)).fetchone()[0])
            point['name'] = version['name']
        selected = next((r for r in current if r['key']==key), None)
        if selected is None and key:
            row = db.execute('''SELECT s.*,v.body FROM traffic_samples s JOIN traffic_versions v ON v.id=s.version_id
                JOIN traffic_readings r ON r.id=s.reading_id WHERE s.route_key=? AND r.source_at>=? AND r.source_at<?
                ORDER BY s.reading_id DESC LIMIT 1''',(key,f['start'],min(f['end'],f['now']+1))).fetchone()
            selected = sample(row) if row else None
        readings = db.execute('SELECT attempted_at,status FROM traffic_readings WHERE attempted_at>=? AND attempted_at<? ORDER BY attempted_at',(f['start'],min(f['end'],f['now']+1))).fetchall()
        start, end = f['start'], min(f['end'],f['now'])
        times = [start]+[r['attempted_at'] for r in readings]+[end]
        gaps = sum(b-a>360 for a,b in zip(times,times[1:]))
        return {'from':f['from'],'to':f['to'],'serverTime':f['now'],'sourceAt':good['source_at'] if good else None,
                'lastAttempt':last['attempted_at'] if last else None,'lastStatus':last['status'] if last else 'empty',
                'stale':not good or f['now']-good['source_at']>360 or not last or last['status']=='failed' or f['now']-last['attempted_at']>360,
                'current':[{k:v for k,v in r.items() if k!='line'} for r in current if r['kind']=='route'],
                'area':meta,'catalog':catalog,'selected':selected,'selectedKey':key,'series':series,'interval':interval,
                'coverage':{'attempts':len(readings),'failures':sum(r['status']=='failed' for r in readings),'gaps':gaps}}
