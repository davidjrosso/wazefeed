"""Reviewed map-template calendars; no browser or background timer is required.

Feed reads materialize a rolling window under the same SQLite writer lock used
by operators. Pauses publish tombstones rather than silently deleting incidents.
"""
import copy
import re
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from catalog import CITY, in_area
from operations import add_run, audit, get_template, schedule_of, stop_run


def local_stamp(day, clock, zone):
    wall = datetime.fromisoformat(day.isoformat()+'T'+clock)
    tz = ZoneInfo(zone)
    candidates = set()
    for fold in (0, 1):
        stamp = wall.replace(tzinfo=tz, fold=fold).timestamp()
        if datetime.fromtimestamp(stamp, tz).replace(tzinfo=None) == wall:
            candidates.add(stamp)
    if len(candidates) != 1:
        raise ValueError(f'{day}: la hora {clock} no existe o es ambigua por el cambio horario. Agregá esa fecha a excepciones.')
    return candidates.pop()


def window(schedule, day):
    end_day = day + timedelta(days=int(schedule['endTime'] < schedule['time']))
    return (local_stamp(day, schedule['time'], schedule['timezone']),
            local_stamp(end_day, schedule['endTime'], schedule['timezone']))


def dates(schedule):
    day, last = date.fromisoformat(schedule['from']), date.fromisoformat(schedule['until'])
    while day <= last:
        if day.weekday() in schedule['weekdays'] and day.isoformat() not in schedule['exceptions']:
            yield day
        day += timedelta(days=1)


def validate_schedule(value, now):
    if not isinstance(value, dict):
        raise ValueError('Completá los datos de la agenda.')
    clean = schedule_of(value)
    for field in ('time', 'endTime'):
        if not isinstance(value.get(field), str) or not re.fullmatch(r'[0-9]{2}:[0-9]{2}', value[field]):
            raise ValueError('Indicá las horas de inicio y fin en formato HH:MM.')
        try:
            datetime.strptime(value[field], '%H:%M')
        except ValueError:
            raise ValueError('Horario inválido.')
    if value['time'] == value['endTime']:
        raise ValueError('Inicio y fin deben ser distintos. Si el fin es anterior, termina al día siguiente.')
    clean.update(endTime=value['endTime'], timezone=CITY['timezone'])
    today = datetime.fromtimestamp(now, ZoneInfo(CITY['timezone'])).date()
    first, last = date.fromisoformat(clean['from']), date.fromisoformat(clean['until'])
    if first < today-timedelta(days=366) or last < today or last > today+timedelta(days=366):
        raise ValueError('La agenda debe incluir fechas pendientes y finalizar dentro del próximo año.')
    if any(not clean['from'] <= d <= clean['until'] for d in clean['exceptions']):
        raise ValueError('Las excepciones deben estar dentro del período de vigencia.')
    if not any(window(clean, d)[1] > now for d in dates(clean)):
        raise ValueError('No quedan fechas por programar con esos días y excepciones.')
    # Check ALL future dates for DST gaps/ambiguities before saving or enabling.
    for d in dates(clean):
        if d >= today-timedelta(days=1):
            start, end = window(clean, d)
            if not 60 <= end-start <= 26*3600:
                raise ValueError('La franja horaria debe durar al menos un minuto y hasta un día local.')
    return clean


def source_problem(template, catalog):
    if template.get('cityId') != CITY['id'] or template.get('catalogVersion') != catalog['version']:
        return 'El catálogo cambió. Pausá la agenda, revisá la plantilla y guardala antes de habilitarla nuevamente.'
    roads = {s['id']: s for s in catalog['segments']}
    for item in template.get('selection', []):
        road = roads.get(item['id'])
        if not road or road['warnings'] or not all(in_area(p) for p in road['points']):
            return 'Hay calles que requieren revisión local. No se generarán nuevas fechas.'
        if item['direction'] not in ('AB','BA','BOTH') or (road['oneway'] and item['direction'] != 'AB'):
            return 'Cambió un sentido de circulación. Revisá la plantilla.'
    if not template.get('selection'):
        return 'La plantilla no contiene calles.'
    if template.get('schedule') and template['schedule'].get('timezone') != CITY['timezone']:
        return 'Cambió la zona horaria de la ciudad. Revisá y guardá la agenda.'
    return None


def preview(template, catalog, now=None, limit=400):
    now = time.time() if now is None else now
    schedule = template.get('schedule')
    problem = source_problem(template, catalog)
    rows = []
    if schedule:
        today = datetime.fromtimestamp(now, ZoneInfo(schedule['timezone'])).date()
        for day in dates(schedule):
            if day < today-timedelta(days=1):
                continue
            try:
                start, end = window(schedule, day)
            except ValueError as error:
                problem = str(error)
                break
            if end > now:
                rows.append({'date':day.isoformat(), 'start':start, 'end':end})
                if len(rows) >= limit:
                    break
    return {'templateId':template['id'], 'templateVersion':template['version'],
            'scheduleVersion':template.get('scheduleVersion', 0), 'enabled':template['enabled'],
            'schedule':schedule, 'occurrences':rows, 'problem':problem, 'serverTime':now}


def change_schedule(state, body, actor, role, catalog, now):
    action = body['action']
    if role != 'admin' and action != 'exclude_map_date':
        raise PermissionError('Solo administración puede configurar, habilitar o pausar agendas.')
    t = get_template(state, body.get('templateId'))
    if t.get('source') != 'map' or t['archived']:
        raise ValueError('Elegí una plantilla del mapa que no esté archivada.')
    if type(body.get('templateVersion')) is not int or body['templateVersion'] != t['version']:
        raise ValueError('La plantilla cambió. Cerrá y volvé a revisar la agenda.')
    if action == 'save_map_schedule':
        if t['enabled']:
            raise ValueError('Pausá la agenda antes de cambiar los horarios o excepciones.')
        t['schedule'] = validate_schedule(body.get('schedule'), now)
        t['scheduleVersion'] = t.get('scheduleVersion', 0)+1
        detail = 'Agenda guardada y desactivada: '+t['name']+'. No publica cierres.'
    elif action == 'enable_map_schedule':
        if t['enabled']:
            raise ValueError('La agenda ya está habilitada.')
        problem = source_problem(t, catalog)
        if problem:
            raise ValueError(problem)
        t['schedule'] = validate_schedule(t.get('schedule'), now)
        t['enabled'] = True
        detail = 'Agenda habilitada: '+t['name']+'. Publicación automática de fechas vigentes y próximas.'
    elif action == 'pause_map_schedule':
        if not t['enabled']:
            raise ValueError('La agenda ya está desactivada.')
        t['enabled'] = False
        for run in state['runs']:
            if run.get('source') == 'map-schedule' and run['templateId'] == t['id'] and run['closedAt'] is None and run['start'] > now:
                stop_run(state, run, now)
                run['paused'] = True
        detail = 'Agenda pausada: '+t['name']+'. Se cancelaron solo sus fechas futuras; los cierres en curso y manuales se conservan.'
    elif action == 'exclude_map_date':
        schedule = t.get('schedule')
        try:
            ds = date.fromisoformat(body.get('date')).isoformat()
        except (TypeError, ValueError):
            raise ValueError('Elegí una fecha válida.')
        if not schedule or not schedule['from'] <= ds <= schedule['until'] or date.fromisoformat(ds).weekday() not in schedule['weekdays']:
            raise ValueError('Esa fecha no corresponde a la agenda.')
        if ds in schedule['exceptions']:
            raise ValueError('Esa fecha ya está suspendida.')
        start, _ = window(schedule, date.fromisoformat(ds))
        if start <= now:
            raise ValueError('La fecha ya comenzó. Para reabrir, finalizá el operativo desde Operativos.')
        schedule['exceptions'] = sorted([*schedule['exceptions'], ds])
        for run in state['runs']:
            if run.get('source') == 'map-schedule' and run['templateId'] == t['id'] and run.get('scheduleDate') == ds and run['closedAt'] is None:
                stop_run(state, run, now)
        detail = 'Fecha suspendida: '+ds+' · '+t['name']+'. El resto de la agenda se conserva.'
    else:
        raise ValueError('Acción de agenda desconocida.')
    t.update(updated=now, author=actor)
    return detail


def materialize_map(state, catalog, now=None):
    now = time.time() if now is None else now
    if not any(t.get('source') == 'map' and t['enabled'] and not t['archived'] and t.get('schedule') for t in state['templates']):
        return state
    result = copy.deepcopy(state)
    keys = {r['key'] for r in state['runs'] if r['key'] and not r.get('paused')}
    count = 0
    for t in result['templates']:
        if t.get('source') != 'map' or not t['enabled'] or t['archived'] or not t.get('schedule') or source_problem(t, catalog):
            continue
        today = datetime.fromtimestamp(now, ZoneInfo(t['schedule']['timezone'])).date()
        for day in dates(t['schedule']):
            if day < today-timedelta(days=1) or day > today+timedelta(days=7):
                continue
            start, end = window(t['schedule'], day)
            if end <= now:
                continue
            ds = day.isoformat()
            key = f"{t['id']}:agenda:{t.get('scheduleVersion', 1)}:{ds}"
            if key in keys:
                continue
            # Resuming/revising during an ongoing date must not duplicate it.
            if any(r.get('source') == 'map-schedule' and r['templateId'] == t['id'] and r.get('scheduleDate') == ds and r['closedAt'] is None and r['end'] > now for r in result['runs']):
                continue
            run = add_run(result, t, start, (end-start)/60, 'programador', now, key)
            run.update(source='map-schedule', scheduleDate=ds, scheduleVersion=t.get('scheduleVersion', 1),
                       cityId=CITY['id'], catalogVersion=t['catalogVersion'])
            keys.add(key)
            count += 1
    if not count:
        return state
    audit(result, 'map_schedule_generate', 'programador', f'{count} fechas de agenda incorporadas al feed.', now)
    return result
