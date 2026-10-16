"""Explicit, bounded OSM data refresh; no map tiles downloaded; no closures created."""
import argparse
import json
import sys
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from catalog import CITY, ROOT, build_catalog

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', type=Path)
    parser.add_argument('--endpoint', default='https://overpass-api.de/api/interpreter')
    parser.add_argument('--output', type=Path, default=ROOT/'config/roads.json')
    args = parser.parse_args()
    if args.input:
        raw = json.loads(args.input.read_text(encoding='utf-8'))
    else:
        box = ','.join(str(x) for x in CITY['bounds'])
        query = f'[out:json][timeout:25][maxsize:67108864];way[highway]({box});(._;>;);out body;'
        request = urllib.request.Request(args.endpoint+'?'+urllib.parse.urlencode({'data': query}),
            headers={'User-Agent': 'MunicipalClosures/1.0 (+https://davidrosso.ar/waze/)'})
        try:
            with urllib.request.urlopen(request, timeout=110) as response:
                raw = json.load(response)
        except urllib.error.HTTPError as error:
            raise RuntimeError(f'HTTP {error.code}: '+error.read(1200).decode('utf-8',errors='replace')) from None
        if raw.get('remark'):
            raise RuntimeError('Overpass reported incomplete data: '+raw['remark'])
    data = build_catalog(raw)
    if len(data['segments']) < 10:
        raise RuntimeError('Catalog unexpectedly small; keeping previous file')
    # Generated data artifact, rebuilt from the upstream dataset, not source code.
    temporary = args.output.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    temporary.replace(args.output)
    print(json.dumps({'segments': len(data['segments']), 'review': sum(bool(s['warnings']) for s in data['segments']),
                      'version': data['version'], 'timestamp': data['sourceTimestamp']}))

if __name__ == '__main__':
    main()
