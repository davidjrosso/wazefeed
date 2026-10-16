"""Linux-only integration test; uses temporary data and never publishes closures."""
import hashlib
import http.client
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
import unittest

from app import App


@unittest.skipUnless(os.name == 'posix' and shutil.which('gunicorn'), 'Requires Linux and Gunicorn')
class GunicornSocketTests(unittest.TestCase):
    def test_private_socket_login_logout_restart_and_empty_feed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            secret = 'isolated-integration-test-only-password'
            salt = '34' * 16
            digest = hashlib.pbkdf2_hmac('sha256', secret.encode(), bytes.fromhex(salt), 600000).hex()
            (root / 'operator.hash').write_text(f'pbkdf2_sha256$600000${salt}${digest}')
            cfg = {**os.environ, 'WAZE_PUBLIC_URL': 'https://davidrosso.ar/waze',
                   'WAZE_DB_PATH': str(root / 'test.sqlite3'),
                   'WAZE_PASSWORD_HASH_FILE': str(root / 'operator.hash'),
                   'WAZE_TRUST_UNIX_SOCKET': '1', 'WAZE_TRUSTED_PROXIES': ''}
            App({**cfg, 'WAZE_INIT_DB': '1'})
            sockpath = str(root / 'test.sock')

            class Connection(http.client.HTTPConnection):
                def connect(self):
                    self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    self.sock.settimeout(3)
                    self.sock.connect(sockpath)

            def request(path, body=None, cookie=None, csrf=None):
                headers = {'Origin': 'https://davidrosso.ar', 'X-Forwarded-For': '192.0.2.10',
                           'Content-Type': 'application/json'}
                if cookie:
                    headers['Cookie'] = cookie
                if csrf:
                    headers['X-CSRF-Token'] = csrf
                conn = Connection('localhost', timeout=3)
                try:
                    conn.request('POST' if body is not None else 'GET', '/waze' + path,
                                 json.dumps(body) if body is not None else None, headers)
                    res = conn.getresponse()
                    return res.status, dict(res.getheaders()), json.loads(res.read())
                finally:
                    conn.close()

            for iteration in range(2):
                with tempfile.TemporaryFile() as log:
                    proc = subprocess.Popen(['gunicorn', '--bind', 'unix:' + sockpath, '--umask', '007',
                                             '--workers', '2', '--worker-tmp-dir', '/tmp',
                                             '--forwarder-headers', '', 'wsgi:application'],
                                            env=cfg, stdout=log, stderr=log)
                    try:
                        for _ in range(80):
                            try:
                                if request('/healthz')[0] == 200:
                                    break
                            except (OSError, http.client.HTTPException):
                                pass
                            if proc.poll() is not None:
                                log.seek(0)
                                self.fail(log.read().decode())
                            time.sleep(0.1)
                        else:
                            self.fail('Gunicorn did not become healthy')
                        self.assertEqual(request('/feed.json')[2], {'incidents': []})
                        self.assertEqual(request('/api/state')[0], 401)
                        status, headers, _ = request('/api/login', {'username': 'operador', 'password': secret})
                        self.assertEqual(status, 200)
                        cookie = headers['Set-Cookie'].split(';')[0]
                        state = request('/api/state', cookie=cookie)[2]
                        self.assertEqual(state['revision'], 0)
                        self.assertEqual(state['events'], [])
                        self.assertIsNone(state['active'])
                        self.assertEqual(request('/api/logout', {}, cookie, state['csrf'])[0], 200)
                        self.assertEqual(request('/api/state', cookie=cookie)[0], 401)
                    finally:
                        proc.terminate()
                        try:
                            proc.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            proc.kill()
                            proc.wait(timeout=5)
