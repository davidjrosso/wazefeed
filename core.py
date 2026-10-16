"""Versioned segment registry and independent, immutable-snapshot CIFS closures."""
import copy
import json
import math
import re
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

SEGMENTS = json.loads((Path(__file__).parent / 'config/segments.json').read_text(encoding='utf-8'))
TZ = timezone(timedelta(hours=-3))


def initial_segments():
    return [{**copy.deepcopy(s), 'id': str(s['id']), 'wazeSegmentId': str(s['id']),
             'direction': 'ONE_DIRECTION', 'archived': False,
             'source': 'OpenStreetMap'} for s in SEGMENTS]


def empty_state():
    return {'schema': 2, 'revision': 0, 'segments': initial_segments(), 'events': [], 'history': []}


def migrate_state(state):
    """Preserve every legacy feed ID, geometry and timestamp; never activate a closure."""
    if state.get('schema') in (2, 3):
        if not all(key in state for key in ('segments', 'events', 'history', 'revision')):
            raise ValueError('Estado incompleto; recuperar respaldo.')
        return state
    if state.get('schema', 1) != 1 or 'segments' in state:
        raise ValueError('Versión de base desconocida; no se modificó.')
    result = copy.deepcopy(state)
    result['schema'] = 2
    result['segments'] = initial_segments()
    result['events'] = []
    for event in state['events']:
        for segment in result['segments']:
            result['events'].append({**copy.deepcopy(event),
                'id': f"{event['id']}:{segment['id']}",
                'feedId': f"rio3-{event['id']}-{segment['id']}",
                'segmentId': segment['id'], 'snapshot': copy.deepcopy(segment),
                'description': 'Cierre por inundación'})
    result['revision'] += 1  # Old open tabs must refresh before any mutation.
    result['history'].insert(0, {'at': time.time(), 'action': 'migrate', 'actor': 'sistema',
                                'detail': 'Registro de tramos habilitado; cierres conservados.'})
    return result


def active_events(state, now=None):
    now = time.time() if now is None else now
    return [e for e in state['events'] if e['closedAt'] is None and e['start'] <= now < e['end']]


def active_event(state, now=None):
    return next(iter(active_events(state, now)), None)


def label(value, name, maximum=120, optional=False):
    if not isinstance(value, str) or not value.isprintable() and value != '':
        raise ValueError(f'{name}: texto inválido.')
    value = value.strip()
    if len(value) > maximum or (not optional and not value):
        raise ValueError(f'{name}: completar con hasta {maximum} caracteres.')
    return value


def validate_segment(data):
    if not isinstance(data, dict):
        raise ValueError('Completar los datos del tramo.')
    result = {key: label(data.get(key), title) for key, title in
              [('street', 'Calle'), ('from', 'Desde'), ('to', 'Hasta')]}
    result['direction'] = data.get('direction')
    if result['direction'] not in ('ONE_DIRECTION', 'BOTH_DIRECTIONS'):
        raise ValueError('Seleccionar el sentido de circulación.')
    waze_id = label(data.get('wazeSegmentId', ''), 'ID de Waze', 20, optional=True)
    if waze_id and not re.fullmatch(r'[0-9]{1,20}', waze_id):
        raise ValueError('El ID de Waze es numérico u opcional.')
    result['wazeSegmentId'] = waze_id
    points = data.get('points')
    if not isinstance(points, list) or not 2 <= len(points) <= 100:
        raise ValueError('Marcar entre 2 y 100 puntos sobre el recorrido.')
    clean = []
    for point in points:
        if (not isinstance(point, list) or len(point) != 2 or
                any(type(n) not in (int, float) or not math.isfinite(n) for n in point)):
            raise ValueError('Coordenadas inválidas.')
        lat, lon = point
        # Service scope is Río Tercero and its surroundings, not worldwide routing.
        if not (-32.5 <= lat <= -31.9 and -64.5 <= lon <= -63.8):
            raise ValueError('El recorrido debe estar en Río Tercero o sus alrededores.')
        pair = [round(lat, 7), round(lon, 7)]
        if clean and pair == clean[-1]:
            raise ValueError('No repetir puntos consecutivos.')
        clean.append(pair)
    length = sum(math.hypot((b[0]-a[0])*111320, (b[1]-a[1])*94200) for a,b in zip(clean, clean[1:]))
    if not 5 <= length <= 20000:
        raise ValueError('El tramo debe medir entre 5 metros y 20 kilómetros.')
    if clean[0] == clean[-1]:
        raise ValueError('El inicio y el final deben ser distintos.')
    result.update(points=clean, source='Trazado manual del operador')
    return result


def change(state, action, revision, actor, now=None, segment_ids=None, segment=None, segment_id=None, description=None):
    now = time.time() if now is None else now
    if type(revision) is not int or revision != state['revision']:
        raise ValueError('El estado cambió. Actualizá el panel antes de intentar nuevamente.')
    state = copy.deepcopy(state)
    current = active_events(state, now)
    by_id = {s['id']: s for s in state['segments']}
    detail = ''
    affected = []
    if action in ('activate', 'reopen'):
        if (not isinstance(segment_ids, list) or not 1 <= len(segment_ids) <= 200 or
                any(not isinstance(s, str) for s in segment_ids) or len(set(segment_ids)) != len(segment_ids)):
            raise ValueError('Seleccionar explícitamente uno o más tramos, sin duplicados.')
        if any(s not in by_id or by_id[s]['archived'] for s in segment_ids):
            raise ValueError('La selección contiene tramos inexistentes o archivados.')
        active_ids = {e['segmentId'] for e in current}
        if action == 'activate':
            if active_ids.intersection(segment_ids):
                raise ValueError('Algún tramo seleccionado ya está cerrado. Actualizá la selección.')
            reason = label(description or 'Cierre por inundación', 'Motivo', 39)
            for sid in segment_ids:
                event_id = str(uuid.uuid4())
                state['events'].append({'id': event_id, 'feedId': f'rio3-{event_id}-{sid}',
                    'segmentId': sid, 'snapshot': copy.deepcopy(by_id[sid]), 'description': reason,
                    'start': now, 'end': now + 3600, 'updated': now, 'closedAt': None})
        else:
            if any(e.get('runId') and e['segmentId'] in segment_ids for e in current):
                raise ValueError('Estos tramos pertenecen a un operativo. Finalizalo desde Operativos, sin afectar otros cierres.')
            if not set(segment_ids).issubset(active_ids):
                raise ValueError('Algún tramo seleccionado no tiene un cierre activo.')
            for event in current:
                if event['segmentId'] in segment_ids:
                    event.update(end=now, updated=now, closedAt=now)
        affected = segment_ids
        detail = '; '.join(f"{by_id[s]['street']}: {by_id[s]['from']} → {by_id[s]['to']}" for s in segment_ids)
    elif action == 'save_segment':
        clean = validate_segment(segment)
        sid = segment.get('id')
        if sid is not None and (not isinstance(sid, str) or sid not in by_id):
            raise ValueError('El tramo a editar no existe.')
        if sid and by_id[sid]['archived']:
            raise ValueError('Restaurar el tramo antes de editarlo.')
        if sid and any(e['segmentId'] == sid for e in current):
            raise ValueError('Reabrir el tramo antes de editar su recorrido o sus datos.')
        if clean['wazeSegmentId'] and any(s['wazeSegmentId'] == clean['wazeSegmentId'] and s['id'] != sid for s in state['segments']):
            raise ValueError('Ese ID de Waze ya está registrado; revisar también los archivados.')
        if any(s['id'] != sid and not s['archived'] and
               (s['points'] == clean['points'] or list(reversed(s['points'])) == clean['points']) for s in state['segments']):
            raise ValueError('Ese recorrido ya está registrado. Revisá el tramo existente.')
        if sid:
            by_id[sid].update(clean)
            detail = 'Tramo editado: ' + clean['street']
        else:
            if len(state['segments']) >= 500:
                raise ValueError('Se alcanzó el límite de 500 tramos registrados.')
            sid = uuid.uuid4().hex
            state['segments'].append({**clean, 'id': sid, 'archived': False})
            detail = 'Tramo agregado: ' + clean['street']
        affected = [sid]
    elif action in ('archive_segment', 'restore_segment'):
        if not isinstance(segment_id, str) or segment_id not in by_id:
            raise ValueError('El tramo no existe.')
        if any(e['segmentId'] == segment_id for e in current):
            raise ValueError('Reabrir el tramo antes de quitarlo del mapa.')
        wanted = action == 'archive_segment'
        if by_id[segment_id]['archived'] == wanted:
            raise ValueError('El tramo ya está en ese estado.')
        if not wanted and any(s['id'] != segment_id and not s['archived'] and
                              (s['points'] == by_id[segment_id]['points'] or
                               list(reversed(s['points'])) == by_id[segment_id]['points']) for s in state['segments']):
            raise ValueError('Ya existe un tramo disponible con ese recorrido.')
        by_id[segment_id]['archived'] = wanted
        detail = ('Tramo archivado: ' if wanted else 'Tramo restaurado: ') + by_id[segment_id]['street']
        affected = [segment_id]
    else:
        raise ValueError('Acción inválida.')
    state['revision'] += 1
    state['history'].insert(0, {'at': now, 'action': action, 'actor': actor, 'segmentIds': affected, 'detail': detail})
    # Keep completed closures/history; feed retention is independent from audit retention.
    return state


def iso(stamp):
    return datetime.fromtimestamp(stamp, TZ).isoformat(timespec='seconds')


def feed(state, now=None):
    now = time.time() if now is None else now
    result = []
    for event in state['events']:
        if event['end'] <= now - 48 * 3600:
            continue
        segment = event['snapshot']
        result.append({'id': event['feedId'], 'type': 'ROAD_CLOSED', 'subtype': event.get('subtype', 'ROAD_CLOSED_HAZARD'),
            'street': segment['street'], 'direction': segment['direction'],
            'polyline': ' '.join(f'{lat:.7f} {lon:.7f}' for lat, lon in segment['points']),
            'description': event['description'], 'starttime': iso(event['start']),
            'endtime': iso(event['end']), 'creationtime': iso(event.get('created', event['start'])), 'updatetime': iso(event['updated'])})
    return {'incidents': result}


def geojson(state=None):
    segments = (state or empty_state())['segments']
    return {'type': 'FeatureCollection', 'features': [
        {'type': 'Feature', 'properties': {'id': s['id'], 'wazeSegmentId': s['wazeSegmentId'],
         'street': s['street'], 'from': s['from'], 'to': s['to'], 'direction': s['direction'], 'source': s['source']},
         'geometry': {'type': 'LineString', 'coordinates': [[lon, lat] for lat, lon in s['points']]}}
        for s in segments if not s['archived']]}
