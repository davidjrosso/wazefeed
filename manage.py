"""Administrative utilities. Never use the development server for production."""
import argparse
import getpass
import hashlib
import json
import os
import secrets
import sqlite3
import string
from pathlib import Path

def valid_password(password):
    return (8 <= len(password) <= 1024 and password.isprintable()
            and any(c in string.ascii_lowercase for c in password)
            and any(c in string.ascii_uppercase for c in password)
            and any(c in string.digits for c in password)
            and any(c in string.punctuation for c in password))

def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command', required=True)
    pw = sub.add_parser('password', help='Create a password hash file; password is never printed')
    pw.add_argument('--output', required=True)
    sub.add_parser('init-db', help='Initialize an empty database only for first installation')
    backup = sub.add_parser('backup')
    backup.add_argument('--output', required=True)
    serve = sub.add_parser('dev')
    serve.add_argument('--port', type=int, default=8766)
    user = sub.add_parser('create-user', help='Create a named account privately; never overwrites an existing account')
    user.add_argument('--name', required=True)
    user.add_argument('--role', choices=['admin', 'operator', 'viewer'], default='operator')
    args = parser.parse_args()
    if args.command in ('password', 'create-user'):
        if args.command == 'password':
            target = Path(args.output)
            if target.exists():
                parser.error('Output exists. Choose a new file; replace the configured file only as an explicit credential rotation.')
        else:
            import re
            if not re.fullmatch(r'[a-zA-Z0-9_.-]{3,40}', args.name) or args.name.lower() == os.environ.get('WAZE_OPERATOR_USER', 'operador').lower():
                parser.error('Nombre inválido o reservado para la cuenta existente.')
        password = getpass.getpass('Nueva contraseña (mínimo 8: mayúscula, minúscula, número y signo): ')
        if not valid_password(password):
            parser.error('Usar entre 8 y 1024 caracteres, con mayúscula, minúscula, número y signo.')
        if password != getpass.getpass('Repetir contraseña: '):
            parser.error('Las contraseñas no coinciden.')
        salt = secrets.token_hex(16)
        digest = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 600000).hex()
        encoded = f'pbkdf2_sha256$600000${salt}${digest}'
        if args.command == 'create-user':
            source = Path(os.environ['WAZE_DB_PATH']).resolve()
            with sqlite3.connect(source.as_uri()+'?mode=rw', uri=True) as db:
                import time
                db.execute('BEGIN IMMEDIATE')
                if db.execute('SELECT 1 FROM accounts WHERE lower(username)=lower(?)', (args.name,)).fetchone():
                    parser.error('Ese usuario ya existe. No se modificó.')
                try:
                    db.execute('INSERT INTO accounts(username,password_hash,role) VALUES(?,?,?)', (args.name, encoded, args.role))
                    db.execute('INSERT INTO account_audit(at,actor,action,subject,role,enabled) VALUES(?,?,?,?,?,1)',
                               (time.time(), 'server-cli', 'create', args.name, args.role))
                except sqlite3.IntegrityError:
                    parser.error('Ese usuario ya existe. No se modificó.')
            print('Cuenta creada con rol '+args.role+'. No se publicaron cierres.')
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(encoded+'\n')
        print('Archivo de credencial creado; la contraseña no se guardó en texto plano.')
    elif args.command == 'backup':
        target = Path(args.output)
        if target.exists():
            parser.error('Output already exists')
        source = Path(os.environ['WAZE_DB_PATH']).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        src = sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        print('Respaldo SQLite consistente creado. Protegerlo: contiene sesiones y registro.')
    else:
        from app import App
        if args.command == 'init-db':
            cfg = dict(os.environ, WAZE_INIT_DB='1')
            if Path(cfg.get('WAZE_DB_PATH', '/var/lib/waze-closures/state.sqlite3')).exists():
                parser.error('Database exists; initialization will not modify it')
            App(cfg)
            print('Base inicializada sin cierres activos.')
        else:
            from wsgiref.simple_server import make_server
            if os.environ.get('WAZE_LOCAL_TEST') != '1':
                parser.error('dev is only available with WAZE_LOCAL_TEST=1')
            print(f'Preview local: http://127.0.0.1:{args.port}/waze/')
            make_server('127.0.0.1', args.port, App()).serve_forever()

if __name__ == '__main__':
    main()
