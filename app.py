"""Standalone WSGI service, mounted with its /waze prefix preserved by the proxy."""
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import re
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from http.cookies import SimpleCookie, CookieError
from pathlib import Path
from urllib.parse import urlsplit
from core import active_event, active_events, change, empty_state, feed, geojson, migrate_state
from operations import upgrade, materialize, operate
from catalog import CITY, load_catalog
from direct import perform
from agenda import materialize_map, preview as agenda_preview
import accounts
import monitoring

ROOT = Path(__file__).resolve().parent

def password_matches(password, encoded):
    try:
        kind, rounds, salt, expected = encoded.split('$')
        if kind != 'pbkdf2_sha256' or not 600000 <= int(rounds) <= 2000000:
            return False
        actual = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), int(rounds)).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False

class HTTPError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message

class App:
    def __init__(self, settings=None):
        cfg = os.environ if settings is None else settings
        self.public_url = cfg.get('WAZE_PUBLIC_URL', 'https://davidrosso.ar/waze').rstrip('/')
        url = urlsplit(self.public_url)
        self.base = url.path.rstrip('/')
        self.origin = f'{url.scheme}://{url.netloc}'
        self.secure = url.scheme == 'https'
        self.local_test = cfg.get('WAZE_LOCAL_TEST') == '1'
        self.monitor_path = cfg.get('WAZE_MONITOR_PATH', '/monitor/current.json')
        self.catalog = load_catalog(cfg.get('WAZE_CATALOG_PATH'))
        if url.query or url.fragment or not url.netloc or not re.fullmatch(r'/[A-Za-z0-9/_-]+', self.base):
            raise ValueError('WAZE_PUBLIC_URL must contain a valid base path, e.g. https://davidrosso.ar/waze')
        if not self.secure and not (cfg.get('WAZE_LOCAL_TEST') == '1' and url.hostname in ('localhost', '127.0.0.1')):
            raise ValueError('HTTPS is required outside explicit loopback tests')
        self.user = cfg.get('WAZE_OPERATOR_USER', 'operador')
        self.password_hash = Path(cfg['WAZE_PASSWORD_HASH_FILE']).read_text(encoding='utf-8').strip()
        parts = self.password_hash.split('$')
        if len(parts) != 4 or parts[0] != 'pbkdf2_sha256' or not 600000 <= int(parts[1]) <= 2000000:
            raise ValueError('Generate the credential file with manage.py password')
        if len(bytes.fromhex(parts[2])) < 16 or len(bytes.fromhex(parts[3])) != 32:
            raise ValueError('Invalid password hash')
        self.auth_tag = hashlib.sha256((self.user + self.password_hash).encode()).hexdigest()
        self.cookie = 'waze_operator'
        # Only for a protected Unix socket whose proxy overwrites the IP header.
        self.trust_unix_socket = cfg.get('WAZE_TRUST_UNIX_SOCKET') == '1'
        self.trusted_proxies = [ipaddress.ip_network(x.strip()) for x in
                                cfg.get('WAZE_TRUSTED_PROXIES', '127.0.0.1/32,::1/128').split(',') if x.strip()]
        self.db_path = Path(cfg.get('WAZE_DB_PATH', '/var/lib/waze-closures/state.sqlite3'))
        new_database = not self.db_path.exists()
        if new_database and cfg.get('WAZE_INIT_DB') != '1':
            raise RuntimeError('Database missing. First installation requires manage.py init-db; do not recreate lost production data.')
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, csrf TEXT NOT NULL, expires REAL NOT NULL, auth_tag TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS attempts (ip TEXT NOT NULL, at REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS attempts_ip_at ON attempts(ip, at);
                CREATE TABLE IF NOT EXISTS accounts (username TEXT PRIMARY KEY, password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('admin','operator')), enabled INTEGER NOT NULL DEFAULT 1);
            ''')
            db.execute('BEGIN IMMEDIATE')
            accounts.initialize(db)
            monitoring.initialize(db)
            columns = {r['name'] for r in db.execute('PRAGMA table_info(sessions)')}
            if 'username' not in columns:
                db.execute("ALTER TABLE sessions ADD COLUMN username TEXT NOT NULL DEFAULT ''")
            if new_database:
                fresh = upgrade(empty_state())
                fresh['revision'] = 0
                db.execute('INSERT INTO state(id,body) VALUES(1,?)', (json.dumps(fresh),))
            original = self.state(db)
            migrated = upgrade(migrate_state(original))
            if migrated is not original:
                db.execute('UPDATE state SET body=? WHERE id=1', (json.dumps(migrated),))

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def state(self, db):
        row = db.execute('SELECT body FROM state WHERE id=1').fetchone()
        if row is None:
            raise sqlite3.DatabaseError('State record missing')
        return json.loads(row['body'])

    def session(self, environ, db):
        cookie = SimpleCookie()
        try:
            cookie.load(environ.get('HTTP_COOKIE', ''))
            token = cookie[self.cookie].value
        except (KeyError, CookieError):
            return None
        if len(token) > 256:
            return None
        key = hashlib.sha256(token.encode()).hexdigest()
        row = db.execute('SELECT * FROM sessions WHERE id=? AND expires>?', (key, time.time())).fetchone()
        if row is None:
            return None
        account = self.account(db, row['username'] or self.user)
        if not account or not hmac.compare_digest(row['auth_tag'], account['tag']):
            return None
        return {**dict(row), 'username': account['username'], 'role': account['role']}

    def account(self, db, username):
        if not isinstance(username, str):
            return None
        # Existing account already had all administrative rights. Preserve it.
        if username == self.user:
            return {'username': self.user, 'password_hash': self.password_hash, 'role': 'admin', 'tag': self.auth_tag}
        row = db.execute('SELECT * FROM accounts WHERE username=? AND enabled=1', (username,)).fetchone()
        if row is None:
            return None
        return {**dict(row), 'tag': hashlib.sha256((username+row['password_hash']+row['role']).encode()).hexdigest()}

    def scheduled_state(self, db):
        # Serialized with every writer; polling from two Waze fetchers is safe.
        db.execute('BEGIN IMMEDIATE')
        original = self.state(db)
        updated = materialize_map(materialize(original), self.catalog)
        if updated is not original:
            db.execute('UPDATE state SET body=? WHERE id=1', (json.dumps(updated),))
        return updated

    def cookie_header(self, token, max_age=43200):
        return ('Set-Cookie', f'{self.cookie}={token}; Path={self.base}/; Max-Age={max_age}; HttpOnly; SameSite=Strict' + ('; Secure' if self.secure else ''))

    def client_ip(self, environ):
        remote = environ.get('REMOTE_ADDR', '')
        try:
            trusted = (not remote and self.trust_unix_socket) or (
                bool(remote) and any(ipaddress.ip_address(remote) in network for network in self.trusted_proxies))
            if trusted:
                value = environ.get('HTTP_X_FORWARDED_FOR', '').strip()
                # Fail closed on invalid chains, never share the proxy's limit.
                if value:
                    return str(ipaddress.ip_address(value))
                if self.trust_unix_socket and not remote:
                    raise HTTPError(400, 'Falta la dirección del cliente del proxy.')
            return str(ipaddress.ip_address(remote))
        except ValueError:
            raise HTTPError(400, 'Dirección del cliente inválida.')

    def body(self, environ):
        if environ.get('HTTP_ORIGIN') != self.origin:
            raise HTTPError(403, 'Origen no permitido.')
        if environ.get('CONTENT_TYPE', '').split(';')[0].strip() != 'application/json':
            raise HTTPError(415, 'Se requiere JSON.')
        try:
            size = int(environ.get('CONTENT_LENGTH') or 0)
            if not 0 < size <= 1048576:
                raise HTTPError(413, 'Tamaño de solicitud inválido.')
            data = json.loads(environ['wsgi.input'].read(size))
        except (ValueError, UnicodeDecodeError):
            raise HTTPError(400, 'JSON inválido.')
        if not isinstance(data, dict):
            raise HTTPError(400, 'Solicitud inválida.')
        return data

    def dispatch(self, environ):
        path, method = environ.get('PATH_INFO', ''), environ.get('REQUEST_METHOD', 'GET')
        if path == self.base and method in ('GET', 'HEAD'):
            return 308, b'', 'text/plain', [('Location', self.base + '/')]
        if not path.startswith(self.base + '/'):
            raise HTTPError(404, 'No encontrado.')
        route = path[len(self.base):]
        if method in ('GET', 'HEAD'):
            if route in ('/', '/login', '/legacy'):
                name = {'/': 'console.html', '/legacy': 'index.html', '/login': 'login.html'}[route]
                html = (ROOT / 'templates' / name).read_text(encoding='utf-8').replace('{{BASE}}', self.base)
                html = html.replace('{{ENV_NOTICE}}', '<div class="test-banner">ENTORNO DE PRUEBAS LOCAL · No publica cierres reales.</div>' if self.local_test else '')
                return 200, html.encode(), 'text/html; charset=utf-8', []
            assets = {'/assets/style.css': ('style.css', 'text/css'), '/assets/client.js': ('client.js', 'text/javascript'),
                      '/assets/console.css': ('console.css', 'text/css'), '/assets/console.js': ('console.js', 'text/javascript'),
                      '/assets/command.css': ('command.css', 'text/css'),
                      '/assets/users.js': ('users.js', 'text/javascript'),
                      '/assets/monitor.js': ('monitor.js', 'text/javascript'),
                      '/assets/monitor.css': ('monitor.css', 'text/css'),
                      '/assets/publication.js': ('publication.js', 'text/javascript'),
                      '/assets/agenda.js': ('agenda.js', 'text/javascript'),
                      '/assets/operations.js': ('operations.js', 'text/javascript'),
                      '/assets/cierres-municipales.user.js': ('cierres-municipales.user.js', 'text/javascript'),
                      '/assets/login.js': ('login.js', 'text/javascript')}
            for name, mime in [('leaflet.js', 'text/javascript'), ('leaflet.css', 'text/css'),
                               ('images/marker-icon.png', 'image/png'), ('images/marker-icon-2x.png', 'image/png'),
                               ('images/marker-shadow.png', 'image/png'), ('images/layers.png', 'image/png'),
                               ('images/layers-2x.png', 'image/png')]:
                assets['/assets/vendor/leaflet/' + name] = ('vendor/leaflet/' + name, mime)
            if route in assets:
                name, mime = assets[route]
                return 200, (ROOT / 'static' / name).read_bytes(), mime + ('; charset=utf-8' if mime.startswith('text/') else ''), []
            with self.db() as db:
                if route == '/roads.json':
                    return self.json(self.catalog)
                if route == '/sector.geojson':
                    return self.json(geojson(self.state(db)), mime='application/geo+json')
                if route == '/healthz':
                    self.state(db)
                    return self.json({'status': 'ok'})
                if route == '/feed.json':
                    return self.json(feed(self.scheduled_state(db)))
                if route in ('/api/state', '/api/catalog', '/api/users', '/api/monitor') or route.startswith(('/api/requests/', '/api/agenda/', '/api/monitor/')):
                    session = self.session(environ, db)
                    if session is None:
                        raise HTTPError(401, 'Iniciá sesión como operador.')
                    if route == '/api/monitor':
                        return self.json(monitoring.listing(db, monitoring.read_cache(self.monitor_path)))
                    if route.startswith('/api/monitor/'):
                        key=route.removeprefix('/api/monitor/')
                        if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',key):
                            raise HTTPError(400, 'Identificador de alerta inválido.')
                        return self.json({'history':monitoring.history(db,key)})
                    if route == '/api/users':
                        if session['role'] != 'admin':
                            raise HTTPError(403, 'Solo administración puede gestionar usuarios.')
                        return self.json(accounts.listing(db, self.user))
                    if route.startswith('/api/agenda/'):
                        original = self.state(db)
                        tid = route.removeprefix('/api/agenda/')
                        template = next((t for t in original['templates'] if t['id'] == tid and t.get('source') == 'map' and not t['archived']), None)
                        if template is None:
                            raise HTTPError(404, 'Plantilla no encontrada.')
                        return self.json({**agenda_preview(template, self.catalog), 'revision':original['revision']})
                    if route.startswith('/api/requests/'):
                        key = route.removeprefix('/api/requests/')
                        # Receipts reveal only this account's completed request, never
                        # another operator's request body, digest or audit information.
                        try:
                            uuid.UUID(key)
                        except (ValueError, AttributeError):
                            raise HTTPError(400, 'Identificador de solicitud inválido.')
                        receipt = next((r for r in self.state(db).get('requests', [])
                                        if r['id'] == key and r['actor'] == session['username']), None)
                        return self.json({'completed': receipt is not None,
                                          'runId': receipt.get('runId') if receipt else None})
                    if route == '/api/catalog':
                        return self.json({'city': CITY, **self.catalog})
                    return self.json(self.operator_state(self.scheduled_state(db), session))
            raise HTTPError(404, 'No encontrado.')
        if method != 'POST':
            raise HTTPError(405, 'Método no permitido.')
        if route not in ('/api/login', '/api/logout', '/api/state', '/api/users', '/api/monitor'):
            raise HTTPError(404, 'No encontrado.')
        body = self.body(environ)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if route == '/api/login':
                ip, now = self.client_ip(environ), time.time()
                db.execute('DELETE FROM attempts WHERE at<?', (now - 900,))
                if db.execute('SELECT COUNT(*) FROM attempts WHERE ip=?', (ip,)).fetchone()[0] >= 5:
                    return self.json({'error': 'Demasiados intentos. Esperá 15 minutos.'}, 429)
                username, password = body.get('username'), body.get('password')
                account = self.account(db, username)
                valid = isinstance(password, str) and len(password) <= 1024 and password_matches(password, account['password_hash'] if account else self.password_hash)
                if not valid or account is None:
                    db.execute('INSERT INTO attempts(ip,at) VALUES(?,?)', (ip, now))
                    return self.json({'error': 'Usuario o contraseña incorrectos.'}, 401)
                token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
                db.execute('DELETE FROM sessions WHERE expires<?', (now,))
                db.execute('DELETE FROM attempts WHERE ip=?', (ip,))
                db.execute('INSERT INTO sessions(id,csrf,expires,auth_tag,username) VALUES(?,?,?,?,?)',
                           (hashlib.sha256(token.encode()).hexdigest(), csrf, now + 43200, account['tag'], account['username']))
                return self.json({'ok': True}, headers=[self.cookie_header(token)])
            session = self.session(environ, db)
            if session is None:
                raise HTTPError(401, 'Iniciá sesión como operador.')
            if not hmac.compare_digest(environ.get('HTTP_X_CSRF_TOKEN', ''), session['csrf']):
                raise HTTPError(403, 'Sesión inválida. Actualizá la página.')
            if route == '/api/logout':
                db.execute('DELETE FROM sessions WHERE id=?', (session['id'],))
                return self.json({'ok': True}, headers=[self.cookie_header('', 0)])
            if route == '/api/users':
                if session['role'] != 'admin':
                    raise HTTPError(403, 'Solo administración puede gestionar usuarios.')
                key, now = 'account:'+session['username'], time.time()
                db.execute('DELETE FROM attempts WHERE at<?', (now-900,))
                if db.execute('SELECT COUNT(*) FROM attempts WHERE ip=?', (key,)).fetchone()[0] >= 5:
                    return self.json({'error': 'Demasiados intentos de confirmación. Esperá 15 minutos.'}, 429)
                password = body.get('adminPassword')
                own = self.account(db, session['username'])
                if not isinstance(password, str) or len(password)>1024 or not password_matches(password, own['password_hash']):
                    db.execute('INSERT INTO attempts(ip,at) VALUES(?,?)', (key, now))
                    return self.json({'error': 'Tu contraseña de administración es incorrecta.'}, 403)
                try:
                    result = accounts.change(db, body, session['username'], self.user)
                except ValueError as error:
                    raise HTTPError(409, str(error))
                db.execute('DELETE FROM attempts WHERE ip=?', (key,))
                return self.json(result)
            if session['role'] == 'viewer':
                raise HTTPError(403, 'Tu cuenta es de consulta: no puede modificar ni publicar cierres.')
            if route == '/api/monitor':
                cache=monitoring.read_cache(self.monitor_path)
                try:
                    monitoring.update(db,cache,body,session['username'])
                except ValueError as error:
                    raise HTTPError(409,str(error))
                return self.json(monitoring.listing(db,cache))
            if body.get('hours', 1) != 1:
                raise HTTPError(400, 'La duración es fija: 1 hora.')
            try:
                if body.get('action') in ('publish_closure', 'reopen_roads', 'report_issue', 'resolve_issue',
                                         'save_map_template', 'archive_map_template', 'save_map_schedule',
                                         'enable_map_schedule', 'pause_map_schedule', 'exclude_map_date'):
                    state = perform(self.state(db), body, session['username'], session['role'], self.catalog)
                elif body.get('action') in ('activate', 'reopen', 'save_segment', 'archive_segment', 'restore_segment'):
                    if session['role'] != 'admin':
                        raise PermissionError('Solo administración puede operar tramos sueltos o modificar calles.')
                    state = change(self.state(db), body.get('action'), body.get('revision'), session['username'],
                                   segment_ids=body.get('segmentIds'), segment=body.get('segment'),
                                   segment_id=body.get('segmentId'), description=body.get('description'))
                else:
                    state = operate(self.state(db), body, session['username'], session['role'])
            except PermissionError as error:
                raise HTTPError(403, str(error))
            except ValueError as error:
                raise HTTPError(409, str(error))
            db.execute('UPDATE state SET body=? WHERE id=1', (json.dumps(state),))
            return self.json(self.operator_state(state, session))

    def operator_state(self, state, session):
        now = time.time()
        public_state = {k: v for k, v in state.items() if k != 'requests'}
        return {**public_state, 'city': CITY, 'catalogVersion': self.catalog['version'], 'active': active_event(state), 'activeEvents': active_events(state),
                'pendingEvents': [e for e in state['events'] if e['closedAt'] is None and e['end'] > now],
                'history': state['history'][:500], 'events': state['events'][-500:], 'csrf': session['csrf'],
                'runs': [r for r in state['runs'] if r['end'] > now and r['closedAt'] is None],
                'user': session['username'], 'role': session['role'], 'serverTime': now}

    def json(self, body, status=200, headers=None, mime='application/json'):
        return status, json.dumps(body, ensure_ascii=False).encode(), mime + '; charset=utf-8', headers or []

    def __call__(self, environ, start_response):
        try:
            status, body, mime, headers = self.dispatch(environ)
        except HTTPError as error:
            status, body, mime, headers = self.json({'error': error.message}, error.status)
        except Exception:
            logging.exception('Request failed (request bodies and credentials are never logged)')
            status, body, mime, headers = self.json({'error': 'Servicio no disponible. No se pudo confirmar el estado.'}, 503)
        headers += [('Content-Type', mime), ('Content-Length', str(len(body))), ('Cache-Control', 'no-store'),
                    ('X-Content-Type-Options', 'nosniff'), ('Referrer-Policy', 'strict-origin-when-cross-origin'),
                    ('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; style-src-attr 'unsafe-inline'; img-src 'self' data: https://tile.openstreetmap.org; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'")]
        start_response(f'{status} ' + {200:'OK',308:'Permanent Redirect',400:'Bad Request',401:'Unauthorized',403:'Forbidden',404:'Not Found',405:'Method Not Allowed',409:'Conflict',413:'Payload Too Large',415:'Unsupported Media Type',429:'Too Many Requests',503:'Service Unavailable'}.get(status, 'Error'), headers)
        return [] if environ.get('REQUEST_METHOD') == 'HEAD' else [body]
