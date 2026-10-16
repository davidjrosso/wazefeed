"""Direct map operations. Geometry is always taken from the server catalog."""
import copy
import hashlib
import json
import math
import time
import uuid
from catalog import CITY, in_area
from core import label
from operations import add_run, audit, get_template, integer, REASONS
from agenda import change_schedule, materialize_map


def selected_segments(body, catalog):
    """Resolve IDs and direction against the current authoritative city catalog."""
    roads = {s['id']: s for s in catalog['segments']}
    if body.get('catalogVersion') != catalog['version'] or not roads:
        raise ValueError('El catálogo cambió o no está disponible. Recargá la página.')
    selection = body.get('selection')
    if not isinstance(selection, list) or not 1 <= len(selection) <= 200:
        raise ValueError('Seleccioná entre 1 y 200 cuadras.')
    segments, seen = [], set()
    for item in selection:
        if not isinstance(item, dict) or not isinstance(item.get('id'), str) or item['id'] not in roads or item['id'] in seen:
            raise ValueError('La selección tiene tramos desconocidos o duplicados.')
        seen.add(item['id'])
        s = copy.deepcopy(roads[item['id']])
        if s['warnings']:
            raise ValueError(s['street']+': requiere revisión local. '+ '; '.join(s['warnings']))
        if not all(in_area(p) for p in s['points']):
            raise ValueError('Una calle está fuera del área habilitada para esta ciudad.')
        direction = item.get('direction')
        if direction not in ('AB', 'BA', 'BOTH') or (s['oneway'] and direction != 'AB'):
            raise ValueError('El sentido elegido no coincide con la circulación de la calle.')
        if direction == 'BA':
            s['points'].reverse()
            s['from'], s['to'] = s['to'], s['from']
        s['direction'] = 'BOTH_DIRECTIONS' if direction == 'BOTH' else 'ONE_DIRECTION'
        s['selectedDirection'] = direction
        segments.append(s)
    return segments


def map_template(state, body):
    template = get_template(state, body.get('templateId'))
    if template.get('source') != 'map' or template['archived']:
        raise ValueError('La plantilla no es del mapa actual o está archivada.')
    if type(body.get('templateVersion')) is not int or body['templateVersion'] != template['version']:
        raise ValueError('La plantilla cambió. Volvé a elegirla y revisá el resumen.')
    return template


def perform(state, body, actor, role, catalog, now=None):
    now = time.time() if now is None else now
    action = body.get('action')
    key = body.get('requestId')
    try:
        uuid.UUID(key)
    except (ValueError, TypeError, AttributeError):
        raise ValueError('Falta el identificador de la solicitud. Actualizá el panel.')
    digest = hashlib.sha256(json.dumps({k: v for k, v in body.items() if k != 'revision'}, sort_keys=True).encode()).hexdigest()
    previous = next((r for r in state.get('requests', []) if r['id'] == key and r['actor'] == actor), None)
    if previous:
        if previous['digest'] != digest:
            raise ValueError('La solicitud ya fue usada con otros datos.')
        return state  # Lost response retry: never duplicate a closure.
    if type(body.get('revision')) is not int or body['revision'] != state['revision']:
        raise ValueError('Otro operador cambió el estado. Actualizá y revisá el resumen nuevamente.')
    result = copy.deepcopy(state)
    roads = {s['id']: s for s in catalog['segments']}
    if action in ('save_map_template', 'archive_map_template'):
        if role != 'admin':
            raise PermissionError('Solo administración puede crear, editar o archivar plantillas.')
        old = map_template(result, body) if body.get('templateId') else None
        if old and old['enabled']:
            raise ValueError('Pausá la agenda antes de editar o archivar esta plantilla.')
        if action == 'archive_map_template':
            if old is None:
                raise ValueError('Elegí una plantilla para archivar.')
            old.update(archived=True, enabled=False, updated=now, author=actor)
            detail = 'Plantilla archivada: '+old['name']+'. Los operativos existentes se conservan.'
        else:
            segments = selected_segments(body, catalog)
            name = label(body.get('name'), 'Nombre de la plantilla', 60)
            reason = body.get('reason')
            if reason not in REASONS:
                raise ValueError('Elegí un motivo válido.')
            description = label(body.get('description') or reason, 'Descripción', 39)
            duration = integer(body.get('durationMinutes'), 1, 10080, 'Duración sugerida en minutos')
            if any(t['name'].casefold() == name.casefold() and not t['archived'] and t['id'] != (old or {}).get('id') for t in result['templates']):
                raise ValueError('Ese nombre de plantilla ya existe.')
            if not old and len(result['templates']) >= 100:
                raise ValueError('Límite de 100 plantillas.')
            template = {'id': old['id'] if old else uuid.uuid4().hex,
                        'version': old['version']+1 if old else 1,
                        'name': name, 'reason': reason, 'description': description,
                        'durationMinutes': duration, 'segments': segments,
                        'selection': [{'id': s['id'], 'direction': s['selectedDirection']} for s in segments],
                        'source': 'map', 'cityId': CITY['id'], 'catalogVersion': catalog['version'],
                        'schedule': copy.deepcopy(old.get('schedule')) if old else None,
                        'scheduleVersion': old.get('scheduleVersion', 0) if old else 0,
                        'enabled': False, 'archived': False,
                        'updated': now, 'author': actor}
            if old:
                old.update(template)
            else:
                result['templates'].append(template)
            detail = 'Plantilla guardada: '+name+'. No activa ni modifica cierres.'
    elif action in ('save_map_schedule', 'enable_map_schedule', 'pause_map_schedule', 'exclude_map_date'):
        detail = change_schedule(result, body, actor, role, catalog, now)
    elif action == 'publish_closure':
        segments = selected_segments(body, catalog)
        saved_template = map_template(result, body) if body.get('templateId') else None
        if saved_template:
            if saved_template['catalogVersion'] != catalog['version'] or saved_template['cityId'] != CITY['id']:
                raise ValueError('El mapa cambió desde que se guardó la plantilla. Administración debe revisarla y guardarla nuevamente.')
            selection = [{'id': s['id'], 'direction': s['selectedDirection']} for s in segments]
            if selection != saved_template['selection']:
                raise ValueError('Las calles o sentidos ya no coinciden con la plantilla. Prepará un cierre nuevo o editá la plantilla.')
        reason = body.get('reason')
        if reason not in REASONS:
            raise ValueError('Elegí un motivo válido.')
        name = label(body.get('name') or reason, 'Nombre del operativo', 60)
        description = label(body.get('description') or reason, 'Descripción', 39)
        start = body.get('start')
        if start == 'NOW':
            start = now
        elif type(start) not in (float, int) or not math.isfinite(start) or not now <= start <= now+366*86400:
            raise ValueError('El inicio programado debe ser futuro y estar dentro del próximo año. Para iniciar ya, elegí AHORA.')
        if body.get('endMode') not in ('at', 'duration'):
            raise ValueError('Finalización inválida.')
        if body.get('endMode') == 'at':
            end = body.get('end')
            if type(end) not in (int, float) or not math.isfinite(end) or not 60 <= end-start <= 7*86400:
                raise ValueError('La finalización debe estar entre un minuto y siete días después del inicio.')
            duration = (end-start)/60
        else:
            duration = integer(body.get('durationMinutes'), 1, 10080, 'Duración en minutos')
        template = {'id': 'direct', 'version': 1, 'name': name, 'reason': reason,
                    'description': description, 'segments': segments}
        if saved_template:
            template.update(id=saved_template['id'], version=saved_template['version'])
        run = add_run(result, template, start, duration, actor, now)
        run.update(source='map-template' if saved_template else 'map', cityId=CITY['id'], catalogVersion=catalog['version'])
        detail = f"Publicado en feed: {name} · {len(segments)} cuadras. Sin confirmación de Waze."
    elif action == 'reopen_roads':
        run = next((r for r in result['runs'] if r['id'] == body.get('runId')), None)
        if not run or run['closedAt'] is not None or run['end'] <= now:
            raise ValueError('El operativo ya terminó o no existe.')
        ids = body.get('segmentIds')
        available = {e['segmentId'] for e in result['events'] if e.get('runId') == run['id'] and e['closedAt'] is None and e['end'] > now}
        if not isinstance(ids, list) or not ids or any(not isinstance(i, str) for i in ids) or not set(ids) <= available:
            raise ValueError('Seleccioná calles pendientes de este operativo.')
        for event in result['events']:
            if event.get('runId') == run['id'] and event['segmentId'] in ids and event['closedAt'] is None:
                if event['start'] > now:
                    event['start'] = now-1
                event.update(end=now, updated=now, closedAt=now)
        if not any(e.get('runId') == run['id'] and e['closedAt'] is None and e['end'] > now for e in result['events']):
            run.update(closedAt=now, end=max(run['start'], now))
            if run.get('source') == 'map-schedule':
                template = get_template(result, run['templateId'])
                if template.get('schedule'):
                    template['schedule']['exceptions'] = sorted(set(template['schedule']['exceptions']) | {run['scheduleDate']})
        detail = f"Reapertura de {len(set(ids))} cuadras en {run['name']}. Otros operativos no se modifican."
    elif action == 'report_issue':
        sid = body.get('segmentId')
        if not isinstance(sid, str) or sid not in roads:
            raise ValueError('Elegí un tramo del mapa.')
        note = label(body.get('note'), 'Detalle del problema', 500)
        issues = result.setdefault('issues', [])
        if sum(i['status'] == 'open' for i in issues) >= 1000:
            raise ValueError('Hay demasiados avisos pendientes; pedí a administración que los revise.')
        issues.append({'id': uuid.uuid4().hex, 'segmentId': sid, 'street': roads[sid]['street'],
                       'osmWayId': roads[sid]['osmWayId'], 'note': note, 'actor': actor,
                       'at': now, 'status': 'open', 'catalogVersion': catalog['version']})
        detail = 'Problema de mapa registrado: '+roads[sid]['street']
    elif action == 'resolve_issue':
        if role != 'admin':
            raise PermissionError('Solo administración puede resolver avisos.')
        issue = next((i for i in result.get('issues', []) if i['id'] == body.get('issueId') and i['status'] == 'open'), None)
        if not issue:
            raise ValueError('El aviso ya se resolvió o no existe.')
        issue.update(status='resolved', resolvedBy=actor, resolvedAt=now)
        detail = 'Aviso marcado como revisado: '+issue['street']
    else:
        raise ValueError('Acción desconocida.')
    receipt = {'id': key, 'actor': actor, 'digest': digest, 'at': now, 'action': action}
    if action == 'publish_closure':
        receipt['runId'] = run['id']
    result.setdefault('requests', []).append(receipt)
    audit(result, action, actor, detail, now)
    return materialize_map(result, catalog, now) if action == 'enable_map_schedule' else result
