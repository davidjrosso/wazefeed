"""Offline OSM road catalog, split at connected junctions (never at visual crossings).

Refresh explicitly with tools/build_catalog.py. Runtime needs no outbound network.
"""
import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CITY = json.loads((ROOT / 'config/city.json').read_text(encoding='utf-8'))
ROAD_TYPES = {'motorway', 'motorway_link', 'trunk', 'trunk_link', 'primary', 'primary_link',
              'secondary', 'secondary_link', 'tertiary', 'tertiary_link', 'unclassified',
              'residential', 'living_street', 'service', 'road'}


def in_area(point, city=CITY):
    south, west, north, east = city['bounds']
    return south <= point[0] <= north and west <= point[1] <= east


def meters(a, b):
    return math.hypot((b[0]-a[0])*111320,
                      (b[1]-a[1])*111320*math.cos(math.radians((a[0]+b[0])/2)))


def build_catalog(raw, city=CITY):
    nodes = {n['id']: [n['lat'], n['lon']] for n in raw['elements'] if n['type'] == 'node'}
    ways = [w for w in raw['elements'] if w['type'] == 'way' and w.get('tags', {}).get('highway') in ROAD_TYPES]
    owners, names = defaultdict(set), defaultdict(set)
    for w in ways:
        for n in w['nodes']:
            owners[n].add(w['id'])
            if w['tags'].get('name'):
                names[n].add(w['tags']['name'])
    segments = []
    for w in ways:
        tags, ids = w['tags'], w['nodes']
        cuts = [0] + [i for i in range(1, len(ids)-1) if len(owners[ids[i]]) > 1] + [len(ids)-1]
        for first, last in zip(cuts, cuts[1:]):
            chunk = ids[first:last+1]
            if len(chunk) < 2 or any(n not in nodes for n in chunk):
                continue
            points = [nodes[n] for n in chunk]
            if not all(in_area(p, city) for p in points):
                continue
            points = [p for i, p in enumerate(points) if i == 0 or p != points[i-1]]
            if len(points) < 2:
                continue
            oneway = tags.get('oneway', '').lower()
            reverse = oneway == '-1'
            one = oneway in ('yes', '1', 'true', '-1') or (not oneway and (tags.get('junction') == 'roundabout' or tags['highway'] == 'motorway'))
            a, b = chunk[0], chunk[-1]
            if reverse:
                points.reverse()
                a, b = b, a
            street = tags.get('name', '')
            start = ' / '.join(sorted(names[a] - {street})) or 'Extremo del tramo'
            end = ' / '.join(sorted(names[b] - {street})) or 'Extremo del tramo'
            length = sum(meters(a, b) for a, b in zip(points, points[1:]))
            warnings = []
            if not street:
                warnings.append('Sin nombre de calle en OSM')
            if tags.get('access') in ('no', 'private') or tags.get('motor_vehicle') == 'no':
                warnings.append('Acceso vehicular restringido; requiere revisión local')
            if any('conditional' in k for k in tags) or oneway not in ('', 'yes', '1', 'true', '-1', 'no', '0', 'false'):
                warnings.append('Circulación especial o condicional; requiere revisión local')
            if points[0] == points[-1]:
                warnings.append('Recorrido circular; requiere dividir el tramo')
            if not 30 <= length <= 20000 or len(points) > 100:
                warnings.append('Longitud o geometría fuera del perfil de publicación')
            sid = f"osm-{w['id']}-{chunk[0]}-{chunk[-1]}"
            segments.append({'id': sid, 'osmWayId': w['id'], 'street': street or 'Calle sin nombre',
                             'from': start, 'to': end, 'points': points, 'oneway': one,
                             'direction': 'ONE_DIRECTION' if one else 'BOTH_DIRECTIONS',
                             'length': round(length), 'source': 'OpenStreetMap',
                             'wazeSegmentId': '', 'warnings': warnings, 'archived': False})
    version = hashlib.sha256(json.dumps(segments, sort_keys=True).encode()).hexdigest()[:20]
    return {'version': version, 'cityId': city['id'], 'sourceTimestamp': raw.get('osm3s', {}).get('timestamp_osm_base'),
            'license': 'ODbL-1.0', 'attribution': '© OpenStreetMap contributors', 'segments': segments}


def load_catalog(path=None):
    path = Path(path) if path else ROOT / 'config/roads.json'
    if not path.exists():
        return {'version': 'missing', 'cityId': CITY['id'], 'segments': [], 'sourceTimestamp': None}
    data = json.loads(path.read_text(encoding='utf-8'))
    if data['cityId'] != CITY['id']:
        raise ValueError('El catálogo no pertenece a la ciudad configurada.')
    return data
