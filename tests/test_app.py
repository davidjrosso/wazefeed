import hashlib
import io
import json
import tempfile
import unittest
import threading
import urllib.request
import http.cookiejar
from wsgiref.simple_server import make_server, WSGIRequestHandler
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from app import App
from core import active_event, change, empty_state, feed, geojson

PASSWORD = 'test-only-not-a-production-password'
SALT = '12' * 16
HASH = 'pbkdf2_sha256$600000$' + SALT + '$' + hashlib.pbkdf2_hmac('sha256', PASSWORD.encode(), bytes.fromhex(SALT), 600000).hex()
IDS = [s['id'] for s in empty_state()['segments']]

class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / 'operator.hash').write_text(HASH)
        self.config = {'WAZE_PUBLIC_URL': 'https://davidrosso.ar/waze', 'WAZE_DB_PATH': str(root / 'state.sqlite3'),
                       'WAZE_PASSWORD_HASH_FILE': str(root / 'operator.hash'), 'WAZE_INIT_DB': '1'}
        self.app = App(self.config)

    def request(self, path, method='GET', body=None, headers=None, app=None):
        payload = json.dumps(body).encode() if body is not None else b''
        environ = {'PATH_INFO': path, 'REQUEST_METHOD': method, 'CONTENT_LENGTH': str(len(payload)),
                   'CONTENT_TYPE': 'application/json', 'wsgi.input': io.BytesIO(payload),
                   'HTTP_ORIGIN': 'https://davidrosso.ar', 'REMOTE_ADDR': '127.0.0.1'}
        environ.update(headers or {})
        result = {}
        def start(status, entries):
            result.update(status=int(status.split()[0]), headers=dict(entries))
        data = b''.join((app or self.app)(environ, start))
        result['body'] = data
        if data and (result['headers']['Content-Type'].startswith('application/json') or '+json' in result['headers']['Content-Type']):
            result['json'] = json.loads(data)
        return result

    def login(self):
        r = self.request('/waze/api/login', 'POST', {'username': 'operador', 'password': PASSWORD})
        self.assertEqual(r['status'], 200)
        cookie = r['headers']['Set-Cookie'].split(';')[0]
        s = self.request('/waze/api/state', headers={'HTTP_COOKIE': cookie})['json']
        return {'HTTP_COOKIE': cookie, 'HTTP_X_CSRF_TOKEN': s['csrf']}, s

    def test_public_routes_and_prefix(self):
        self.assertEqual(self.request('/waze')['headers']['Location'], '/waze/')
        self.assertEqual(self.request('/waze/feed.json')['json'], {'incidents': []})
        self.assertEqual(self.request('/feed.json')['status'], 404)
        self.assertEqual(self.request('/wazee/feed.json')['status'], 404)
        self.assertEqual(self.request('/waze/assets/../../app.py')['status'], 404)
        html = self.request('/waze/')['body'].decode()
        self.assertIn('/waze/assets/console.js', html)
        self.assertIn('/waze/login', html)
        self.assertNotIn('signin-with-chatgpt', html)
        self.assertIn('Seleccioná. Revisá. Publicá.', html)
        self.assertIn('Cerrar seleccionados por 1 hora', self.request('/waze/legacy')['body'].decode())
        self.assertEqual(self.request('/waze/healthz')['status'], 200)
        self.assertEqual(self.request('/waze/feed.json', 'HEAD')['body'], b'')

    def test_auth_and_csrf(self):
        self.assertEqual(self.request('/waze/api/state')['status'], 401)
        self.assertEqual(self.request('/waze/api/state', headers={'HTTP_OAI_AUTHENTICATED_USER_EMAIL': 'owner@example.test'})['status'], 401)
        headers, state = self.login()
        self.assertEqual(self.request('/waze/api/state', 'POST', {'action': 'activate', 'revision': 0}, {'HTTP_COOKIE': headers['HTTP_COOKIE']})['status'], 403)
        self.assertEqual(self.request('/waze/api/state', 'POST', {'action': 'activate', 'revision': 0}, {**headers, 'HTTP_ORIGIN': 'https://evil.test'})['status'], 403)
        self.assertEqual(self.request('/waze/api/state', 'POST', {'action': 'activate', 'revision': 0, 'hours': 2}, headers)['status'], 400)

    def test_one_hour_and_reopen_persist(self):
        headers, state = self.login()
        result = self.request('/waze/api/state', 'POST', {'action': 'activate', 'revision': 0, 'segmentIds': IDS}, headers)
        self.assertEqual(result['status'], 200)
        event = result['json']['active']
        self.assertEqual(event['end'] - event['start'], 3600)
        original = self.request('/waze/feed.json')['json']['incidents']
        self.assertEqual(len(original), 4)
        self.assertEqual(len({i['id'] for i in original}), 4)
        self.assertTrue(all(i['direction'] == 'ONE_DIRECTION' and len(i['polyline'].split()) == 6 for i in original))
        cfg = {k: v for k, v in self.config.items() if k != 'WAZE_INIT_DB'}
        restarted = App(cfg)
        self.assertEqual(self.request('/waze/api/state', headers=headers, app=restarted)['json']['active']['id'], event['id'])
        self.assertEqual(self.request('/waze/api/state', 'POST', {'action': 'activate', 'revision': 0}, headers)['status'], 409)
        reopened = self.request('/waze/api/state', 'POST', {'action': 'reopen', 'revision': 1, 'segmentIds': IDS}, headers)
        self.assertIsNone(reopened['json']['active'])
        final = self.request('/waze/feed.json')['json']['incidents']
        self.assertEqual([i['id'] for i in original], [i['id'] for i in final])
        self.assertEqual([i['starttime'] for i in original], [i['starttime'] for i in final])
        self.assertEqual(self.request('/waze/api/logout', 'POST', {}, headers)['status'], 200)
        self.assertEqual(self.request('/waze/api/state', headers=headers)['status'], 401)

    def test_cookie_and_rate_limit(self):
        r = self.request('/waze/api/login', 'POST', {'username': 'operador', 'password': PASSWORD})
        for flag in ('Path=/waze/', 'Secure', 'HttpOnly', 'SameSite=Strict'):
            self.assertIn(flag, r['headers']['Set-Cookie'])
        for _ in range(5):
            self.assertEqual(self.request('/waze/api/login', 'POST', {'username': 'bad', 'password': 'bad'})['status'], 401)
        self.assertEqual(self.request('/waze/api/login', 'POST', {'username': 'operador', 'password': PASSWORD})['status'], 429)

    def test_concurrent_clicks_create_one_episode(self):
        headers, _ = self.login()
        def click(_):
            return self.request('/waze/api/state', 'POST', {'action': 'activate', 'revision': 0, 'segmentIds': IDS}, headers)['status']
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(click, range(2))), [200, 409])
        self.assertEqual(len(self.request('/waze/feed.json')['json']['incidents']), 4)

    def test_database_error_is_not_empty_feed(self):
        with self.app.db() as db:
            db.execute('DELETE FROM state')
        with self.assertLogs(level='ERROR'):
            self.assertEqual(self.request('/waze/feed.json')['status'], 503)
        with self.assertRaises(Exception):
            App(self.config)

    def test_unix_proxy_rate_limits_are_per_client(self):
        app = App({**self.config, 'WAZE_TRUST_UNIX_SOCKET': '1', 'WAZE_TRUSTED_PROXIES': ''})
        first = {'REMOTE_ADDR': '', 'HTTP_X_FORWARDED_FOR': '192.0.2.10'}
        second = {'REMOTE_ADDR': '', 'HTTP_X_FORWARDED_FOR': '192.0.2.11'}
        for _ in range(5):
            self.assertEqual(self.request('/waze/api/login', 'POST', {'username': 'bad', 'password': 'bad'}, first, app)['status'], 401)
        self.assertEqual(self.request('/waze/api/login', 'POST', {'username': 'operador', 'password': PASSWORD}, first, app)['status'], 429)
        self.assertEqual(self.request('/waze/api/login', 'POST', {'username': 'operador', 'password': PASSWORD}, second, app)['status'], 200)

    def test_invalid_forwarded_chain_does_not_poison_shared_limit(self):
        headers = {'REMOTE_ADDR': '127.0.0.1', 'HTTP_X_FORWARDED_FOR': '192.0.2.10, 192.0.2.11'}
        for _ in range(6):
            self.assertEqual(self.request('/waze/api/login', 'POST', {'username': 'bad', 'password': 'bad'}, headers)['status'], 400)
        self.assertEqual(self.request('/waze/api/login', 'POST', {'username': 'operador', 'password': PASSWORD})['status'], 200)

    def test_untrusted_tcp_peer_cannot_forge_client_ip(self):
        env = {'REMOTE_ADDR': '192.0.2.20', 'HTTP_X_FORWARDED_FOR': '192.0.2.99'}
        self.assertEqual(self.app.client_ip(env), '192.0.2.20')

    def test_unix_requires_explicit_trust_and_single_ip(self):
        from app import HTTPError
        with self.assertRaises(HTTPError):
            self.app.client_ip({'REMOTE_ADDR': '', 'HTTP_X_FORWARDED_FOR': '192.0.2.10'})
        app = App({**self.config, 'WAZE_TRUST_UNIX_SOCKET': '1'})
        with self.assertRaises(HTTPError):
            app.client_ip({'REMOTE_ADDR': ''})
        with self.assertRaises(HTTPError):
            app.client_ip({'REMOTE_ADDR': '', 'HTTP_X_FORWARDED_FOR': '192.0.2.10, 192.0.2.11'})

    def test_missing_database_requires_explicit_initialization(self):
        cfg = {**self.config, 'WAZE_DB_PATH': str(Path(self.tmp.name) / 'missing.sqlite3'), 'WAZE_INIT_DB': '0'}
        with self.assertRaises(RuntimeError):
            App(cfg)

    def test_expiry_and_geometry(self):
        state = change(empty_state(), 'activate', 0, 'test', now=1000, segment_ids=IDS)
        self.assertIsNotNone(active_event(state, 4599))
        self.assertIsNone(active_event(state, 4600))
        self.assertEqual(len(feed(state, 4600)['incidents']), 4)
        self.assertEqual(feed(state, 4600 + 48 * 3600), {'incidents': []})
        self.assertEqual(geojson()['features'][0]['geometry']['coordinates'][0], [-64.1173807, -32.1728093])

    def test_real_http_login_and_button_flow(self):
        class QuietHandler(WSGIRequestHandler):
            def log_message(self, *args):
                pass
        server = make_server('127.0.0.1', 0, self.app, handler_class=QuietHandler)
        origin = f'http://127.0.0.1:{server.server_port}'
        server.set_app(App({**self.config, 'WAZE_PUBLIC_URL': origin + '/waze', 'WAZE_LOCAL_TEST': '1'}))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
            def request(path, body=None, csrf=None):
                headers = {'Origin': origin, 'Content-Type': 'application/json'}
                if csrf:
                    headers['X-CSRF-Token'] = csrf
                req = urllib.request.Request(origin + '/waze' + path, headers=headers,
                      data=json.dumps(body).encode() if body is not None else None)
                with opener.open(req, timeout=5) as r:
                    return r.read()
            request('/api/login', {'username': 'operador', 'password': PASSWORD})
            state = json.loads(request('/api/state'))
            started = json.loads(request('/api/state', {'action': 'activate', 'revision': state['revision'], 'segmentIds': IDS}, state['csrf']))
            self.assertEqual(started['active']['end'] - started['active']['start'], 3600)
            self.assertEqual(len(json.loads(request('/feed.json'))['incidents']), 4)
            self.assertIn(b'/waze/assets/console.js', request('/'))
            request('/api/state', {'action': 'reopen', 'revision': started['revision'], 'segmentIds': IDS}, state['csrf'])
            self.assertIsNone(json.loads(request('/api/state'))['active'])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

if __name__ == '__main__':
    unittest.main()
