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
    except (ValueError, OverflowError):
        raise ValueError('Fechas o filtros inválidos.') from None
    if not 0 <= (end_day-start_day).days < 93 or end_day > today:
        raise ValueError('Elegí hasta 93 días, sin fechas futuras.')
    kind, street = get('type'), get('street').strip()
    if kind not in ('', *LABELS) or len(street) > 120 or not 0 <= minimum <= 10 or not 0 <= offset <= 1000000 or not 0 <= after <= 2**63-1:
        raise ValueError('Filtros fuera de rango.')
    return {'from': start_day.isoformat(), 'to': end_day.isoformat(),
            'start': datetime.combine(start_day, daytime(), zone).timestamp(),
            'end': datetime.combine(end_day+timedelta(days=1), daytime(), zone).timestamp(),
            'type': kind, 'street': street, 'reliability': minimum, 'offset': offset, 'after': after,
            'timezone': timezone, 'now': now, 'days': (end_day-start_day).days+1}


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
            and normalized(f['street']) in normalized(row.get('street', ''))
            and (not f['reliability'] or reliability is not None and reliability >= f['reliability']))


def dashboard(path, f):
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
        return {'filters': {k: f[k] for k in ('from','to','type','street','reliability','timezone')},
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
        return {'report': report_row(row),
                'events': [{**t, 'body': json.loads(t['body'])} for t in transitions[:100]],
                'hasMore': len(transitions)>100, 'nextCursor': transitions[99]['id'] if len(transitions)>100 else None}
