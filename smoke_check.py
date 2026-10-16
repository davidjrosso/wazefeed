"""Read-only deployment check; never activates closures. No credentials required."""
import argparse
import json
import urllib.error
import urllib.request

def fetch(url):
    try:
        with urllib.request.urlopen(url, timeout=15) as response:
            return response.status, response.headers, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.headers, error.read()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='https://davidrosso.ar/waze')
    args = parser.parse_args()
    base = args.url.rstrip('/')
    for path in ('/', '/login', '/legacy', '/assets/client.js', '/assets/style.css',
                 '/assets/console.js', '/assets/console.css', '/assets/command.css', '/assets/users.js', '/assets/publication.js', '/assets/agenda.js',
                 '/assets/monitor.js', '/assets/monitor.css', '/healthz', '/sector.geojson'):
        status, _, _ = fetch(base + path)
        assert status == 200, (path, status)
    status, headers, body = fetch(base + '/feed.json')
    assert status == 200 and headers.get_content_type() == 'application/json'
    payload = json.loads(body)
    assert isinstance(payload.get('incidents'), list)
    assert 'no-store' in headers.get('Cache-Control', '')
    for path in ('/api/state', '/api/catalog', '/api/users', '/api/monitor', '/api/monitor/example', '/api/agenda/not-an-id',
                 '/api/requests/00000000-0000-4000-8000-000000000000'):
        status, _, _ = fetch(base + path)
        assert status == 401, ('Private endpoint must reject anonymous requests', path, status)
    print('OK: public feed and assets; operator state protected; no changes made.')
    print('Feed contains', len(payload['incidents']), 'entries (including recently ended closures).')

if __name__ == '__main__':
    main()
