"""Read-only analytical views of archived Waze observations."""
from collections import Counter
from contextlib import closing
from datetime import datetime, timedelta, time as daytime
import json
import math
import re
import time
import unicodedata
from urllib.parse import parse_qs
from zoneinfo import ZoneInfo

from observation_store import connect_readonly, summary

LABELS = {'pothole': 'Baches', 'flood': 'Inundaciones', 'accident': 'Accidentes',
          'closure': 'Cierres reportados', 'hazard': 'Otros peligros', 'other': 'Otros'}


def category(body):
    subtype = body.get('subtype', '')
    if 'POT_HOLE' in subtype:
        return 'pothole'
    if 'FLOOD' in subtype:
        return 'flood'
    return {'ACCIDENT': 'accident', 'ROAD_CLOSED': 'closure', 'HAZARD': 'hazard',
            'WEATHERHAZARD': 'hazard'}.get(body.get('type'), 'other')


def normalized(value):
    return ''.join(c for c in unicodedata.normalize('NFD', value.casefold()) if not unicodedata.combining(c))


def filters(query, timezone, now=None):
    now = time.time() if now is None else now
    zone = ZoneInfo(timezone)
    today = datetime.fromtimestamp(now, zone).date()
    if len(query) > 2000:
        raise ValueError('Filtros demasiado largos.')
    args = parse_qs(query, keep_blank_values=True, max_num_fields=20)
    if any(len(v) != 1 for v in args.values()):
        raise ValueError('Filtros repetidos.')
    get = lambda k, default='': args.get(k, [default])[0]
    try:
        start_day = datetime.strptime(get('from', (today-timedelta(days=6)).isoformat()), '%Y-%m-%d').date()
        end_day = datetime.strptime(get('to', today.isoformat()), '%Y-%m-%d').date()
        offset = int(get('offset', '0'))
        after = int(get('after', '0'))
        minimum = int(get('reliability', '0'))
        zone_id = int(get('zone', '0'))
        at = float(get('at', str(now)))
    except (ValueError, OverflowError):
        raise ValueError('Fechas o filtros inválidos.') from None
    if not 0 <= (end_day-start_day).days < 93 or end_day > today:
        raise ValueError('Elegí hasta 93 días, sin fechas futuras.')
    kind, street = get('type'), get('street').strip()
    if kind not in ('', *LABELS) or len(street) > 120 or not 0 <= minimum <= 10 or not 0 <= offset <= 1000000 or not 0 <= after <= 2**63-1 or not 0<=zone_id<=2**63-1 or not math.isfinite(at):
        raise ValueError('Filtros fuera de rango.')
    return {'from': start_day.isoformat(), 'to': end_day.isoformat(),
            'start': datetime.combine(start_day, daytime(), zone).timestamp(),
            'end': datetime.combine(end_day+timedelta(days=1), daytime(), zone).timestamp(),
            'type': kind, 'street': street, 'reliability': minimum, 'offset': offset, 'after': after,
            'timezone': timezone, 'now': now, 'days': (end_day-start_day).days+1,
            'zoneId':zone_id,'zone':None,'at':at}


SCOPED = '''WITH scoped AS (
    SELECT s.report_id, MAX(s.reading_id) AS last_reading,
        MIN(rd.attempted_at) AS period_first, MAX(rd.attempted_at) AS period_last,
        COUNT(*) AS observations
    FROM readings rd JOIN sightings s ON s.reading_id=rd.id
    WHERE rd.attempted_at>=? AND rd.attempted_at<?
    GROUP BY s.report_id
)
SELECT p.*,r.first_seen,r.imported,v.body FROM scoped p
JOIN reports r ON r.id=p.report_id
JOIN sightings s ON s.report_id=p.report_id AND s.reading_id=p.last_reading
JOIN versions v ON v.id=s.version_id'''


def report_row(row):
    body = json.loads(row['body'])
    return {**body, 'firstSeen': row['first_seen'], 'periodFirst': row['period_first'],
            'periodLast': row['period_last'], 'observations': row['observations'],
            'imported': bool(row['imported']), 'category': category(body)}


def matches(row, f):
    reliability = row.get('reliability')
    return ((not f['type'] or row['category'] == f['type'])
            and (not f.get('zone') or distance(row['point'],[f['zone']['lat'],f['zone']['lon']])<=f['zone']['radius'])
            and normalized(f['street']) in normalized(row.get('street', ''))
            and (not f['reliability'] or reliability is not None and reliability >= f['reliability']))


def dashboard(path, f, state_db=None):
    with closing(connect_readonly(path)) as db:
        deadline = time.monotonic()+12
        db.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
        db.execute('BEGIN')  # One consistent snapshot, even while the collector commits.
        metadata = summary(db)
        rows = [report_row(r) for r in db.execute(SCOPED, (f['start'], f['end']))]
        rows = [r for r in rows if matches(r, f)]
        by_id = {r['id']: r for r in rows}
        step = 3600 if f['days'] == 1 else 86400
        count = 24 if f['days'] == 1 else f['days']
        zone = ZoneInfo(f['timezone'])
        buckets = [{'at': f['start']+i*step,
                    'label': datetime.fromtimestamp(f['start']+i*step, zone).strftime('%H:%M' if step == 3600 else '%d/%m'),
                    'observed': 0, 'published': 0, 'readings': 0, 'failed': 0, 'partial': 0}
                   for i in range(count)]
        successful, failed, partial, repeated = 0, 0, 0, 0
        read_times = []
        for rd in db.execute('SELECT attempted_at,status,repeated_source FROM readings WHERE attempted_at>=? AND attempted_at<? ORDER BY attempted_at', (f['start'], f['end'])):
            i = int((rd['attempted_at']-f['start'])//step)
            if not 0 <= i < count:
                continue
            buckets[i]['readings'] += 1
            if rd['status'] == 'failed':
                failed += 1
                buckets[i]['failed'] += 1
            else:
                successful += 1
                read_times.append(rd['attempted_at'])
            if rd['status'] == 'partial':
                partial += 1
                buckets[i]['partial'] += 1
            repeated += rd['repeated_source']
        # Group in SQL before transferring rows: one UUID per bucket, not per poll.
        for hit in db.execute('''SELECT CAST((rd.attempted_at-?)/? AS INTEGER) AS bucket,s.report_id
                FROM readings rd JOIN sightings s ON s.reading_id=rd.id
                WHERE rd.attempted_at>=? AND rd.attempted_at<? GROUP BY bucket,s.report_id''',
                (f['start'], step, f['start'], f['end'])):
            if hit['report_id'] in by_id and 0 <= hit['bucket'] < count:
                buckets[hit['bucket']]['observed'] += 1
        for row in rows:
            i = int((row['publishedAt']-f['start'])//step)
            if 0 <= i < count:
                buckets[i]['published'] += 1
        left = max(f['start'], metadata['startedAt'] or f['start'])
        right = min(f['end'], f['now'])
        boundaries = [left]+[t for t in read_times if left <= t <= right]+[right]
        gaps = [b-a for a, b in zip(boundaries, boundaries[1:]) if b-a > 360] if right > left else []
        types = Counter(r['category'] for r in rows)
        streets = Counter(r['street'] or 'Calle sin nombre' for r in rows)
        reliability = Counter(r['reliability'] for r in rows if r.get('reliability') is not None)
        known = sum(reliability.values())
        rows.sort(key=lambda r: (-r['periodLast'], r['id']))
        metadata['stale'] = not metadata['lastSuccess'] or f['now']-metadata['lastSuccess'] > 360
        analysis = analyze(db, rows, f, metadata)
        if state_db is not None:
            from history_tools import enrich
            analysis.update(enrich(state_db,rows))
        for row in rows:
            row['spanSeconds'] = max(0, row['periodLast']-row['periodFirst'])
        return {'analysis': analysis, 'filters': {k: f[k] for k in ('from','to','type','street','reliability','timezone')},
                'meta': metadata, 'total': len(rows), 'offset': f['offset'], 'limit': 50,
                'reports': rows[f['offset']:f['offset']+50], 'series': buckets,
                'metrics': {'unique': len(rows),
                            'published': sum(f['start'] <= r['publishedAt'] < f['end'] for r in rows),
                            'firstDetected': sum(not r['imported'] and f['start'] <= r['firstSeen'] < f['end'] for r in rows),
                            'averageReliability': round(sum(k*v for k,v in reliability.items())/known, 1) if known else None,
                            'reliabilityKnown': known},
                'types': [{'key': k, 'label': LABELS[k], 'count': v} for k,v in types.most_common()],
                'streets': [{'label': k, 'count': v} for k,v in streets.most_common(10)],
                'reliability': [{'score': i, 'count': reliability[i]} for i in sorted(set(range(11)) | set(reliability))],
                'coverage': {'successful': successful, 'failed': failed, 'partial': partial, 'repeated': repeated,
                             'gaps': len(gaps), 'largestGapMinutes': math.ceil(max(gaps, default=0)/60),
                             'beforeArchive': f['start'] < (metadata['startedAt'] or f['start']),
                             'firstReading': read_times[0] if read_times else None,
                             'lastReading': read_times[-1] if read_times else None}}


def distance(a,b):
    lat1,lat2=map(math.radians,(a[0],b[0]))
    dlat=lat2-lat1;dlon=math.radians(b[1]-a[1])
    h=math.sin(dlat/2)**2+math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
    return 6371000*2*math.asin(min(1,math.sqrt(h)))


def replay(path,f):
    if not f['start']<=f['at']<f['end'] or f['at']>f['now']:
        raise ValueError('Elegí un instante del período, sin horas futuras.')
    with closing(connect_readonly(path)) as db:
        deadline=time.monotonic()+12
        db.set_progress_handler(lambda:int(time.monotonic()>deadline),10000)
        db.execute('BEGIN')
        rd=db.execute('SELECT id,attempted_at,status FROM readings WHERE attempted_at>=? AND attempted_at<=? ORDER BY attempted_at DESC,id DESC LIMIT 1',(f['start'],f['at'])).fetchone()
        if not rd or f['at']-rd['attempted_at']>360:
            return {'at':f['at'],'status':'gap','readingAt':rd['attempted_at'] if rd else None,'reports':[],'total':None,'truncated':False}
        rows=[]
        if rd['status']!='failed':
            for hit in db.execute('SELECT v.body FROM sightings s JOIN versions v ON v.id=s.version_id WHERE s.reading_id=? ORDER BY s.report_id',(rd['id'],)):
                body=json.loads(hit['body']);body['category']=category(body)
                if matches(body,f):rows.append(body)
        return {'at':f['at'],'status':rd['status'],'readingAt':rd['attempted_at'],'reports':rows[:1000],
                'total':len(rows) if rd['status']!='failed' else None,'truncated':len(rows)>1000}


def detail(path, uid, f):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', uid):
        raise ValueError('Identificador de reporte inválido.')
    with closing(connect_readonly(path)) as db:
        deadline = time.monotonic()+12
        db.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
        db.execute('BEGIN')
        row = db.execute(SCOPED+' WHERE p.report_id=?', (f['start'], f['end'], uid)).fetchone()
        if row is None:
            return None
        transitions = [dict(r) for r in db.execute('''SELECT t.id,t.at,t.kind,v.body FROM transitions t
            JOIN versions v ON v.id=t.version_id WHERE t.report_id=? AND t.at>=? AND t.at<? AND t.id>?
            ORDER BY t.id LIMIT 101''', (uid, f['start'], f['end'], f['after']))]
        return {'report': report_row(row), 'evolution': evolution(db, uid, f),
                'events': [{**t, 'body': json.loads(t['body'])} for t in transitions[:100]],
                'hasMore': len(transitions)>100, 'nextCursor': transitions[99]['id'] if len(transitions)>100 else None}


def coverage_window(db, start, end, archive_start):
    reads = list(db.execute('SELECT attempted_at,status FROM readings WHERE attempted_at>=? AND attempted_at<? ORDER BY attempted_at', (start,end)))
    good = [r['attempted_at'] for r in reads if r['status'] != 'failed']
    gaps = [b-a for a,b in zip([start]+good, good+[end]) if b-a > 360]
    return {'readings': len(reads), 'successful': len(good),
            'complete': bool(good) and archive_start is not None and archive_start <= start+120 and not gaps and all(r['status']=='ok' for r in reads),
            'gapMinutes': round(sum(gaps)/60), 'partial': sum(r['status']=='partial' for r in reads)}


def analyze(db, rows, f, metadata):
    """Distinct reports, report-days and spatial cells; never inferred incidents."""
    end = min(f['end'], f['now'])
    duration = max(0, end-f['start'])
    # Shift the previous window by whole selected days, then match elapsed time.
    previous_start = f['start']-f['days']*86400
    previous_end = previous_start+duration
    previous = [report_row(r) for r in db.execute(SCOPED, (previous_start,previous_end))]
    previous = [r for r in previous if matches(r,f)]
    current_coverage = coverage_window(db,f['start'],end,metadata['startedAt'])
    previous_coverage = coverage_window(db,previous_start,previous_end,metadata['startedAt'])
    comparable = current_coverage['complete'] and previous_coverage['complete']
    by_id = {r['id']:r for r in rows}
    days = [{'day': (datetime.fromisoformat(f['from'])+timedelta(days=i)).date().isoformat(),
             'observed':0,'published':0} for i in range(f['days'])]
    hour_sets = [set() for _ in range(24)]
    hour_readings = [0]*24
    for rd in db.execute("SELECT attempted_at FROM readings WHERE attempted_at>=? AND attempted_at<? AND status!='failed'",(f['start'],end)):
        hour_readings[int((rd['attempted_at']-f['start'])//3600)%24]+=1
    day_sets = [set() for _ in days]
    report_days = {r['id']:set() for r in rows}
    for hit in db.execute('''SELECT CAST((rd.attempted_at-?)/3600 AS INTEGER) AS slot,s.report_id
            FROM readings rd JOIN sightings s ON s.reading_id=rd.id
            WHERE rd.attempted_at>=? AND rd.attempted_at<? GROUP BY slot,s.report_id''', (f['start'],f['start'],end)):
        uid, slot = hit['report_id'], hit['slot']
        day = slot//24
        if uid in by_id and 0 <= day < len(days):
            day_sets[day].add(uid); hour_sets[slot%24].add((day,uid)); report_days[uid].add(day)
    for i,day in enumerate(days):
        start = f['start']+i*86400
        day.update(observed=len(day_sets[i]), published=sum(start<=r['publishedAt']<min(start+86400,end) for r in rows),
                   coverage=coverage_window(db,start,min(start+86400,end),metadata['startedAt']) if start<end else {'complete':False,'readings':0},
                   inProgress=start+86400>f['now'])
    cells = {}
    lat_step = 100/111320
    lon_step = 100/(111320*math.cos(math.radians(-32.173)))
    for row in rows:
        lat,lon = row['point']
        key = (math.floor(lat/lat_step), math.floor(lon/lon_step))
        cell = cells.setdefault(key, {'reports':0,'days':set(),'streets':Counter(), 'ids':[],
                                     'point':[(key[0]+.5)*lat_step,(key[1]+.5)*lon_step]})
        cell['reports']+=1; cell['days'].update(report_days[row['id']]); cell['streets'][row['street'] or 'Calle sin nombre']+=1
        cell['ids'].append(row['id'])
    locations = [{'point':c['point'], 'reports':c['reports'], 'days':len(c['days']),
                  'label':' / '.join(k for k,v in c['streets'].most_common(2)), 'ids':sorted(c['ids'])[:50]} for c in cells.values()]
    locations.sort(key=lambda c:(-c['days'],-c['reports'],c['point']))
    spans = sorted(max(0,r['periodLast']-r['periodFirst']) for r in rows)
    n=len(spans)
    median = (spans[(n-1)//2]+spans[n//2])/2 if n else None
    bands = [('Una sola observación',lambda x:x==0),('Menos de 1 hora',lambda x:0<x<3600),
             ('De 1 a 6 horas',lambda x:3600<=x<21600),('De 6 a 24 horas',lambda x:21600<=x<86400),('24 horas o más',lambda x:x>=86400)]
    return {'comparison':{'current':len(rows),'previous':len(previous),'previousStart':previous_start,'previousEnd':previous_end,
                          'comparable':comparable, 'currentCoverage':current_coverage,'previousCoverage':previous_coverage,
                          'changePercent':round((len(rows)-len(previous))*100/len(previous),1) if comparable and previous else None},
            'days':days,'hours':[{'label':f'{i:02d}:00','count':len(s),'readings':hour_readings[i]} for i,s in enumerate(hour_sets)],
            'locations':locations[:100],'locationTotal':len(locations),
            'duration':{'medianSeconds':median,'bands':[{'label':label,'count':sum(test(s) for s in spans)} for label,test in bands]}}


def evolution(db, uid, f):
    # One point per consecutive version, plus its last sighting. Cap payload, not calculation.
    points=[]; first=last=None; intervals=0; gaps=0; previous_version=None
    for hit in db.execute('''SELECT rd.attempted_at,s.version_id,v.body FROM sightings s
            JOIN readings rd ON rd.id=s.reading_id JOIN versions v ON v.id=s.version_id
            WHERE s.report_id=? AND rd.attempted_at>=? AND rd.attempted_at<? ORDER BY rd.attempted_at,rd.id''', (uid,f['start'],f['end'])):
        at=hit['attempted_at']
        if first is None:first=at
        if last is not None:
            if at-last<=360:intervals+=at-last
            else:gaps+=1
        if hit['version_id']!=previous_version:
            body=json.loads(hit['body'])
            points.append({'at':at,'until':at,**{k:body.get(k) for k in ('reliability','confidence','thumbsUp')}})
        else:points[-1]['until']=at
        last=at;previous_version=hit['version_id']
    state=db.execute('SELECT kind,at FROM transitions WHERE report_id=? AND at<? ORDER BY at DESC,id DESC LIMIT 1',(uid,f['end'])).fetchone()
    return {'points':points[:500], 'truncated':len(points)>500, 'spanSeconds':max(0,(last or 0)-(first or 0)),
            'sampledSeconds':intervals,'gaps':gaps,'lastState':dict(state) if state else None}


RESEARCH_STATUSES = ('pending','investigating','confirmed','unverified','discarded')


def initialize_notes(db):
    db.execute('''CREATE TABLE IF NOT EXISTS history_notes (
        id INTEGER PRIMARY KEY AUTOINCREMENT, report_id TEXT NOT NULL,
        at REAL NOT NULL, actor TEXT NOT NULL, status TEXT NOT NULL, note TEXT NOT NULL)''')
    db.execute('CREATE INDEX IF NOT EXISTS history_notes_report ON history_notes(report_id,id)')


def notes(db, uid):
    rows=[dict(r) for r in db.execute('SELECT id,at,actor,status,note FROM history_notes WHERE report_id=? ORDER BY id DESC LIMIT 100',(uid,))]
    return {'entries':rows,'revision':rows[0]['id'] if rows else 0}


def add_note(db, path, body, actor):
    uid=body.get('id'); note=body.get('note'); status=body.get('status')
    if not isinstance(uid,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',uid):
        raise ValueError('Identificador de reporte inválido.')
    if status not in RESEARCH_STATUSES or not isinstance(note,str) or not note.strip() or len(note)>2000:
        raise ValueError('Elegí un estado y escribí una nota de hasta 2000 caracteres.')
    with closing(connect_readonly(path)) as archive:
        if not archive.execute('SELECT 1 FROM reports WHERE id=?',(uid,)).fetchone():
            raise ValueError('El reporte no existe en el archivo.')
    current=notes(db,uid)
    if type(body.get('revision')) is not int or body['revision']!=current['revision']:
        raise ValueError('Hay un seguimiento más reciente. Reabrí la ficha antes de guardar; tu nota no se guardó.')
    db.execute('INSERT INTO history_notes(report_id,at,actor,status,note) VALUES(?,?,?,?,?)',(uid,time.time(),actor,status,note.strip()))
    return notes(db,uid)
