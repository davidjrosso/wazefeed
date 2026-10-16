"""Administrative account operations; credentials never enter operational state."""
import hashlib
import re
import secrets
import time
from manage import valid_password

ROLES = ('admin', 'operator', 'viewer')


def initialize(db):
    # SQLite cannot extend a CHECK constraint in place. Copy every existing row
    # inside App's startup transaction; sessions and passwords remain unchanged.
    sql = db.execute("SELECT sql FROM sqlite_master WHERE name='accounts'").fetchone()[0]
    if "'viewer'" not in sql:
        db.execute("CREATE TABLE accounts_new (username TEXT PRIMARY KEY, password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('admin','operator','viewer')), enabled INTEGER NOT NULL DEFAULT 1)")
        db.execute('INSERT INTO accounts_new SELECT username,password_hash,role,enabled FROM accounts')
        db.execute('DROP TABLE accounts')
        db.execute('ALTER TABLE accounts_new RENAME TO accounts')
    db.execute('CREATE TABLE IF NOT EXISTS account_audit (id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL, subject TEXT NOT NULL, role TEXT NOT NULL, enabled INTEGER NOT NULL)')


def listing(db, emergency):
    return {
        'revision': db.execute('SELECT COALESCE(MAX(id),0) FROM account_audit').fetchone()[0],
        'accounts': [{'username': emergency, 'role': 'admin', 'enabled': True, 'protected': True}] + [
            {**dict(r), 'enabled': bool(r['enabled']), 'protected': False}
            for r in db.execute('SELECT username,role,enabled FROM accounts WHERE username<>? ORDER BY lower(username)', (emergency,))],
        'audit': [dict(r) for r in db.execute('SELECT * FROM account_audit ORDER BY id DESC LIMIT 50')],
    }


def encode_password(value):
    if not isinstance(value, str) or not valid_password(value):
        raise ValueError('La contraseña debe tener entre 8 y 1024 caracteres, con mayúscula, minúscula, número y signo.')
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', value.encode(), bytes.fromhex(salt), 600000).hex()
    return f'pbkdf2_sha256$600000${salt}${digest}'


def change(db, body, actor, emergency):
    current = listing(db, emergency)
    if type(body.get('revision')) is not int or body['revision'] != current['revision']:
        raise ValueError('La lista cambió. Actualizala y revisá los datos antes de confirmar.')
    action, name = body.get('action'), body.get('username')
    if not isinstance(name, str) or not re.fullmatch(r'[a-zA-Z0-9_.-]{3,40}', name):
        raise ValueError('Usuario: entre 3 y 40 letras, números, punto, guion o guion bajo.')
    if name.lower() == emergency.lower():
        raise ValueError('La cuenta de recuperación está protegida; se administra desde el servidor.')
    row = db.execute('SELECT username,role,enabled FROM accounts WHERE username=?', (name,)).fetchone()
    if action == 'create':
        if db.execute('SELECT 1 FROM accounts WHERE lower(username)=lower(?)', (name,)).fetchone():
            raise ValueError('Ese usuario ya existe. No se modificó.')
        role = body.get('role')
        if role not in ROLES:
            raise ValueError('Rol inválido.')
        encoded = encode_password(body.get('password'))
        db.execute('INSERT INTO accounts(username,password_hash,role) VALUES(?,?,?)', (name, encoded, role))
        enabled = True
    elif action in ('access', 'password'):
        if row is None:
            raise ValueError('Cuenta no encontrada.')
        role, enabled = row['role'], bool(row['enabled'])
        if action == 'access':
            role, enabled = body.get('role'), body.get('enabled')
            if role not in ROLES or type(enabled) is not bool:
                raise ValueError('Rol o estado inválido.')
            if name == actor and (not enabled or role != 'admin'):
                raise ValueError('No podés desactivar tu propia cuenta ni quitarte la administración.')
            db.execute('UPDATE accounts SET role=?,enabled=? WHERE username=?', (role, int(enabled), name))
        else:
            encoded = encode_password(body.get('password'))
            db.execute('UPDATE accounts SET password_hash=? WHERE username=?', (encoded, name))
        # Prevent disabled sessions coming back to life after reenabling an account.
        db.execute('DELETE FROM sessions WHERE username=?', (name,))
    else:
        raise ValueError('Acción de usuarios desconocida.')
    db.execute('INSERT INTO account_audit(at,actor,action,subject,role,enabled) VALUES(?,?,?,?,?,?)',
               (time.time(), actor, action, name, role, int(enabled)))
    return listing(db, emergency)
