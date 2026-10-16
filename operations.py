"""Shared, versioned templates and independently owned closure operations.

Scheduling is materialized transactionally on feed/state reads: future dated
incidents are served ahead of time, without a browser or a background thread.
"""
import copy
import time
import uuid
from datetime import datetime, date, timedelta
from core import TZ, label, validate_segment

REASONS = {'Inundación': 'ROAD_CLOSED_HAZARD', 'Obras': 'ROAD_CLOSED_CONSTRUCTION',
           'Accidente': 'ROAD_CLOSED_HAZARD',
           'Escuelas': 'ROAD_CLOSED_EVENT', 'Evento': 'ROAD_CLOSED_EVENT', 'Otro': 'ROAD_CLOSED_HAZARD'}


def upgrade(state):
    if state.get('schema') == 3:
        for key in ('templates', 'runs', 'segments', 'events', 'history', 'revision'):
            if key not in state:
                raise ValueError('Estado de operativos incompleto.')
        return state
    if state.get('schema') != 2:
        raise ValueError('Primero migrar el registro de tramos.')
    result = copy.deepcopy(state)
    result.update(schema=3, templates=[], runs=[])
    result['revision'] += 1
    return result


def integer(value, minimum, maximum, name):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f'{name}: usar un entero entre {minimum} y {maximum}.')
    return value


def schedule_of(value):
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError('Horario inválido.')
    try:
        first, last = date.fromisoformat(value['from']), date.fromisoformat(value['until'])
        start = datetime.strptime(value['time'], '%H:%M').time()
        days = value['weekdays']
        if not isinstance(days, list) or not days or len(days) > 7 or len(set(days)) != len(days):
            raise ValueError()
        for day in days:
            integer(day, 0, 6, 'Día')
        exceptions = value.get('exceptions', [])
        if not isinstance(exceptions, list) or len(exceptions) > 366:
            raise ValueError()
        exceptions = sorted({date.fromisoformat(d).isoformat() for d in exceptions})
        if last < first or (last-first).days > 366:
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise ValueError('Revisá fechas, hora, días y excepciones del calendario (máximo un año).')
    return {'from': first.isoformat(), 'until': last.isoformat(), 'time': start.strftime('%H:%M'),
            'weekdays': sorted(days), 'exceptions': exceptions}


def audit(state, action, actor, detail, now):
    state['revision'] += 1
    state['history'].insert(0, {'at': now, 'action': action, 'actor': actor, 'detail': detail})


def get_template(state, template_id):
    template = next((t for t in state['templates'] if t['id'] == template_id), None)
    if template is None:
        raise ValueError('La plantilla no existe.')
    return template


def add_run(state, template, start, duration, actor, now, key=None):
    rid = str(uuid.uuid4())
    run = {'id': rid, 'templateId': template['id'], 'version': template['version'],
           'name': template['name'], 'reason': template['reason'], 'start': start,
           'end': start+duration*60, 'closedAt': None, 'actor': actor, 'key': key,
           'segments': copy.deepcopy(template['segments']), 'created': now}
    state['runs'].append(run)
    for segment in run['segments']:
        eid = str(uuid.uuid4())
        state['events'].append({'id': eid, 'feedId': 'municipal-'+eid, 'runId': rid,
            'segmentId': segment['id'], 'snapshot': copy.deepcopy(segment),
            'description': template['description'], 'subtype': REASONS[template['reason']],
            'start': start, 'end': run['end'], 'updated': now, 'created': now, 'closedAt': None})
    return run


def stop_run(state, run, now):
    cancelled = run['start'] > now
    run['end'] = max(run['start'], now)
    run['closedAt'] = now
    for event in state['events']:
        if event.get('runId') == run['id'] and event['closedAt'] is None:
            event.update(end=max(event['start'], now), closedAt=now, updated=now, cancelled=cancelled)
            if cancelled:
                # CIFS permits changing the start of FUTURE closures. Publish a
                # stable-ID, already-ended tombstone instead of silently omitting
                # an incident Waze may already have fetched. Keep the original
                # planned interval in the run audit snapshot.
                event.update(start=now-1, end=now)


def materialize(state, now=None):
    """Idempotent rolling seven-day publication, including today's ongoing slot."""
    now = time.time() if now is None else now
    result = copy.deepcopy(state)
    today = datetime.fromtimestamp(now, TZ).date()
    keys = {r['key'] for r in state['runs'] if r['key'] and not r.get('paused')}
    count = 0
    for template in result['templates']:
        if template.get('source') == 'map':
            continue  # Current-catalog calendars are handled by agenda.materialize_map.
        schedule = template['schedule']
        if not template['enabled'] or template['archived'] or not schedule:
            continue
        for offset in range(-1, 8):
            day = today + timedelta(days=offset)
            ds = day.isoformat()
            if not (schedule['from'] <= ds <= schedule['until']) or day.weekday() not in schedule['weekdays'] or ds in schedule['exceptions']:
                continue
            start = datetime.fromisoformat(ds+'T'+schedule['time']).replace(tzinfo=TZ).timestamp()
            # Never manufacture missed historical operations after an outage.
            if start+template['durationMinutes']*60 <= now:
                continue
            key = f"{template['id']}:{template['version']}:{ds}"
            if key not in keys:
                if any(r['templateId'] == template['id'] and r['closedAt'] is None and r['start'] < start+template['durationMinutes']*60 and start < r['end'] for r in result['runs']):
                    continue
                add_run(result, template, start, template['durationMinutes'], 'programador', now, key)
                keys.add(key)
                count += 1
    if not count:
        return state
    audit(result, 'schedule_generate', 'programador', f'{count} operativos programados incorporados al feed.', now)
    return result


def operate(state, body, actor, role, now=None):
    now = time.time() if now is None else now
    if type(body.get('revision')) is not int or body['revision'] != state['revision']:
        raise ValueError('El estado cambió. Actualizá el panel y revisá nuevamente.')
    result = copy.deepcopy(state)
    action = body.get('action')
    if action in ('save_template', 'archive_template', 'schedule_enable', 'schedule_pause', 'import_segments') and role != 'admin':
        raise PermissionError('Solo administración puede cambiar plantillas, calles y calendarios.')
    # Map templates must pass current-catalog and version checks in direct.perform.
    # Do not let the historical admin UI bypass these checks or start schedules.
    tid = (body.get('template') or {}).get('id') if action == 'save_template' and isinstance(body.get('template'), dict) else body.get('templateId')
    if action in ('save_template', 'archive_template', 'schedule_enable', 'schedule_pause', 'activate_template') and tid:
        if get_template(result, tid).get('source') == 'map':
            raise ValueError('Esta plantilla se gestiona desde Plantillas en la consola nueva.')
    if action == 'import_segments':
        bundle = body.get('bundle')
        if not isinstance(bundle, dict) or bundle.get('format') != 'municipal-closures-segments' or bundle.get('schema') != 1:
            raise ValueError('Archivo de calles incompatible.')
        items = bundle.get('segments')
        if not isinstance(items, list) or not 1 <= len(items) <= 200:
            raise ValueError('Importar entre 1 y 200 tramos por archivo.')
        for item in items:
            clean = validate_segment(item)
            clean['source'] = 'Selección Waze · revisión municipal'
            existing = next((s for s in result['segments'] if clean['wazeSegmentId'] and s['wazeSegmentId'] == clean['wazeSegmentId']), None)
            if existing:
                if existing['points'] != clean['points'] or existing['direction'] != clean['direction'] or existing['archived']:
                    raise ValueError('Un ID importado ya existe con otro trazado, sentido o está archivado. Revisalo antes de importar.')
                continue
            if any(s['points'] == clean['points'] or list(reversed(s['points'])) == clean['points'] for s in result['segments']):
                raise ValueError('Hay un recorrido duplicado. Revisá los tramos existentes.')
            if len(result['segments']) >= 500:
                raise ValueError('Límite de 500 tramos.')
            result['segments'].append({**clean, 'id': uuid.uuid4().hex, 'archived': False})
        detail = 'Importación revisada de calles; no se publicaron cierres.'
    elif action == 'save_template':
        data = body.get('template')
        if not isinstance(data, dict):
            raise ValueError('Completar la plantilla.')
        name = label(data.get('name'), 'Nombre', 60)
        reason = data.get('reason')
        if reason not in REASONS:
            raise ValueError('Motivo inválido.')
        description = label(data.get('description'), 'Descripción', 39)
        duration = integer(data.get('durationMinutes'), 5, 1440, 'Duración en minutos')
        ids = data.get('segmentIds')
        if not isinstance(ids, list) or not 1 <= len(ids) <= 200 or any(not isinstance(i,str) for i in ids) or len(set(ids)) != len(ids):
            raise ValueError('Elegir entre 1 y 200 tramos, sin duplicados.')
        by_id = {s['id']: s for s in result['segments'] if not s['archived']}
        if any(i not in by_id for i in ids):
            raise ValueError('La plantilla incluye tramos inexistentes o archivados.')
        tid = data.get('id')
        old = get_template(result, tid) if tid else None
        if old and (old['enabled'] or old['archived']):
            raise ValueError('Pausá el calendario antes de editar. Las plantillas archivadas no se editan.')
        if any(t['name'].casefold() == name.casefold() and t['id'] != tid and not t['archived'] for t in result['templates']):
            raise ValueError('Ese nombre de plantilla ya existe.')
        if not old and len(result['templates']) >= 100:
            raise ValueError('Límite de 100 plantillas.')
        template = {'id': tid or uuid.uuid4().hex, 'version': old['version']+1 if old else 1,
                    'name': name, 'reason': reason, 'description': description,
                    'durationMinutes': duration, 'segments': [copy.deepcopy(by_id[i]) for i in ids],
                    'schedule': schedule_of(data.get('schedule')), 'enabled': False, 'archived': False,
                    'updated': now, 'author': actor}
        if old:
            old.update(template)
        else:
            result['templates'].append(template)
        detail = 'Plantilla guardada: '+name+'. No activa cierres.'
    elif action in ('archive_template', 'schedule_enable', 'schedule_pause'):
        template = get_template(result, body.get('templateId'))
        if template['archived']:
            raise ValueError('La plantilla está archivada.')
        if action == 'schedule_enable':
            if not template['schedule'] or template['enabled']:
                raise ValueError('Configurá un calendario que no esté ya habilitado.')
            if template['schedule']['until'] < datetime.fromtimestamp(now, TZ).date().isoformat():
                raise ValueError('El calendario ya finalizó.')
            template['enabled'] = True
        else:
            template['enabled'] = False
            for run in result['runs']:
                if run['templateId'] == template['id'] and run['key'] and run['closedAt'] is None and run['start'] > now:
                    stop_run(result, run, now)
                    run['paused'] = True
            if action == 'archive_template':
                template['archived'] = True
        detail = f"{action}: {template['name']}. Los operativos en curso se finalizan por separado."
    elif action == 'activate_template':
        template = get_template(result, body.get('templateId'))
        if template['archived']:
            raise ValueError('Plantilla archivada.')
        if any(r['templateId'] == template['id'] and r['closedAt'] is None and r['start'] <= now < r['end'] for r in result['runs']):
            raise ValueError('Esta plantilla ya tiene un operativo activo. Extendelo si hace falta.')
        add_run(result, template, now, template['durationMinutes'], actor, now)
        detail = 'Operativo activado: '+template['name']
    elif action in ('finish_run', 'extend_run'):
        run = next((r for r in result['runs'] if r['id'] == body.get('runId')), None)
        if not run or run['closedAt'] is not None or run['end'] <= now:
            raise ValueError('El operativo ya terminó o no existe.')
        if action == 'finish_run':
            stop_run(result, run, now)
            if run.get('source') == 'map-schedule':
                template = get_template(result, run['templateId'])
                if template.get('schedule'):
                    template['schedule']['exceptions'] = sorted(set(template['schedule']['exceptions']) | {run['scheduleDate']})
        else:
            minutes = integer(body.get('minutes'), 5, 1440, 'Extensión')
            if run['start'] > now or run['end']+minutes*60-run['start'] > 7*86400:
                raise ValueError('Solo se extienden operativos activos, hasta un máximo de 7 días.')
            run['end'] += minutes*60
            for event in result['events']:
                if event.get('runId') == run['id'] and event['closedAt'] is None:
                    event.update(end=run['end'], updated=now)
        detail = f"{action}: {run['name']}. Solo afecta a este operativo."
    else:
        raise ValueError('Acción de operativo inválida.')
    audit(result, action, actor, detail, now)
    return materialize(result, now)
