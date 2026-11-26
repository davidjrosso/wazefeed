"""Durable Waze observations. Separate from closure state and municipal decisions.

Only the collector writes this SQLite file. No automatic retention/deletion.
Times are Unix seconds (UTC); published_at is NOT the incident's occurrence time.
"""
import argparse
from contextlib import closing, contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time

from monitoring import merge


SCHEMA = '''
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS readings (
    id INTEGER PRIMARY KEY, attempted_at REAL NOT NULL,
    source_at REAL, status TEXT NOT NULL, error TEXT,
    observed_count INTEGER NOT NULL DEFAULT 0, invalid_count INTEGER NOT NULL DEFAULT 0,
    outside_count INTEGER NOT NULL DEFAULT 0, repeated_source INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS readings_time ON readings(attempted_at);
CREATE TABLE IF NOT EXISTS reports (
    id TEXT PRIMARY KEY, published_at REAL NOT NULL,
    first_seen REAL NOT NULL, last_seen REAL NOT NULL, present INTEGER NOT NULL,
    version_id INTEGER NOT NULL, imported INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS reports_published ON reports(published_at);
CREATE TABLE IF NOT EXISTS versions (
    id INTEGER PRIMARY KEY, report_id TEXT NOT NULL REFERENCES reports(id) DEFERRABLE INITIALLY DEFERRED,
    fingerprint TEXT NOT NULL, body TEXT NOT NULL,
    UNIQUE(report_id, fingerprint)
);
CREATE TABLE IF NOT EXISTS sightings (
    reading_id INTEGER NOT NULL REFERENCES readings(id),
    report_id TEXT NOT NULL REFERENCES reports(id),
    version_id INTEGER NOT NULL REFERENCES versions(id),
    PRIMARY KEY(reading_id, report_id)
);
CREATE INDEX IF NOT EXISTS sightings_report ON sightings(report_id, reading_id);
CREATE TABLE IF NOT EXISTS transitions (
    id INTEGER PRIMARY KEY, report_id TEXT NOT NULL REFERENCES reports(id),
    reading_id INTEGER REFERENCES readings(id), at REAL NOT NULL,
    kind TEXT NOT NULL, version_id INTEGER NOT NULL REFERENCES versions(id)
);
CREATE INDEX IF NOT EXISTS transitions_report ON transitions(report_id, id);
'''


def body_for(alert):
    return {k: v for k, v in alert.items() if k not in ('firstSeen', 'lastSeen', 'present', 'case')}


@contextmanager
def database(path):
    path = Path(path)
    # Exclusive creation gives the database private permissions from the outset.
    if not path.exists():
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o640)
            os.close(fd)
        except FileExistsError:
            pass
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    try:
        # Rollback journal supports read-only consumers without writable WAL/SHM.
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA synchronous=FULL')
        version = db.execute('PRAGMA user_version').fetchone()[0]
        if version not in (0, 1):
            raise ValueError('Unsupported history schema')
        db.executescript(SCHEMA)
        db.execute('PRAGMA user_version=1')
        db.execute('BEGIN IMMEDIATE')
        with db:
            yield db
    finally:
        db.close()


def version_for(db, alert):
    body = json.dumps(body_for(alert), ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    fingerprint = hashlib.sha256(body.encode()).hexdigest()
    db.execute('INSERT OR IGNORE INTO versions(report_id,fingerprint,body) VALUES(?,?,?)',
               (alert['id'], fingerprint, body))
    return db.execute('SELECT id FROM versions WHERE report_id=? AND fingerprint=?',
                      (alert['id'], fingerprint)).fetchone()[0]


def bootstrap(db, previous, now):
    """Preserve available cache records, explicitly marked as an import, not a replay."""
    if db.execute("SELECT 1 FROM settings WHERE key='startedAt'").fetchone():
        return
    db.execute("INSERT INTO settings VALUES('startedAt',?)", (str(now),))
    for alert in previous.get('alerts', []):
        version = version_for(db, alert)
        db.execute('INSERT INTO reports VALUES(?,?,?,?,?,?,1)',
                   (alert['id'], alert['publishedAt'], alert['firstSeen'], alert['lastSeen'],
                    int(bool(alert['present'])), version))
        db.execute('INSERT INTO transitions(report_id,at,kind,version_id) VALUES(?,?,?,?)',
                   (alert['id'], now, 'imported', version))


def summary(db):
    started = db.execute("SELECT value FROM settings WHERE key='startedAt'").fetchone()
    last = db.execute('SELECT * FROM readings ORDER BY id DESC LIMIT 1').fetchone()
    success = db.execute("SELECT attempted_at FROM readings WHERE status IN ('ok','partial') ORDER BY id DESC LIMIT 1").fetchone()
    return {'startedAt': float(started[0]) if started else None,
            'lastAttempt': last['attempted_at'] if last else None,
            'lastSuccess': success[0] if success else None,
            'status': last['status'] if last else 'empty',
            'reports': db.execute('SELECT count(*) FROM reports').fetchone()[0],
            'readings': db.execute('SELECT count(*) FROM readings').fetchone()[0]}


def record(path, previous, payload, bounds, now, error=None):
    """Atomically archive one polling attempt. Failures never imply disappearance."""
    with database(path) as db:
        bootstrap(db, previous, now)
        area = json.dumps(bounds)
        saved_area = db.execute("SELECT value FROM settings WHERE key='bounds'").fetchone()
        if saved_area and saved_area[0] != area:
            # A smaller area must not manufacture disappearances in the archive.
            error = 'area_changed'
        elif not saved_area:
            db.execute("INSERT INTO settings VALUES('bounds',?)", (area,))
        old = db.execute('SELECT source_at FROM readings WHERE source_at IS NOT NULL ORDER BY id DESC LIMIT 1').fetchone()
        fresh = None
        if error is None:
            try:
                fresh = merge({'meta': {'sourceTime': old[0] if old else 0}}, payload, bounds, now)
            except (ValueError, TypeError):
                error = 'invalid_feed'
        meta = fresh['meta'] if fresh else {}
        status = ('partial' if meta.get('invalidCount') else 'ok') if fresh else 'failed'
        cursor = db.execute('''INSERT INTO readings(attempted_at,source_at,status,error,
            observed_count,invalid_count,outside_count,repeated_source) VALUES(?,?,?,?,?,?,?,?)''',
            (now, meta.get('sourceTime'), status, error, len(fresh['alerts']) if fresh else 0,
             meta.get('invalidCount', 0), meta.get('outsideCount', 0),
             int(bool(old and meta.get('sourceTime') == old[0]))))
        reading = cursor.lastrowid
        if fresh:
            seen = set()
            for alert in fresh['alerts']:
                uid = alert['id']
                seen.add(uid)
                prior = db.execute('SELECT * FROM reports WHERE id=?', (uid,)).fetchone()
                version = version_for(db, alert)
                kinds = []
                if prior is None:
                    kinds.append('new')
                    db.execute('INSERT INTO reports VALUES(?,?,?,?,?,?,0)',
                               (uid, alert['publishedAt'], now, now, 1, version))
                else:
                    if not prior['present']:
                        kinds.append('reappeared')
                    if prior['version_id'] != version:
                        kinds.append('changed')
                    db.execute('UPDATE reports SET published_at=?,last_seen=?,present=1,version_id=? WHERE id=?',
                               (alert['publishedAt'], now, version, uid))
                db.execute('INSERT INTO sightings VALUES(?,?,?)', (reading, uid, version))
                for kind in kinds:
                    db.execute('INSERT INTO transitions(report_id,reading_id,at,kind,version_id) VALUES(?,?,?,?,?)',
                               (uid, reading, now, kind, version))
            # Partial reads cannot establish absence. No guessed resolution time.
            if status == 'ok':
                missing = [r for r in db.execute('SELECT id,version_id FROM reports WHERE present=1') if r['id'] not in seen]
                for row in missing:
                    db.execute('UPDATE reports SET present=0 WHERE id=?', (row['id'],))
                    db.execute('INSERT INTO transitions(report_id,reading_id,at,kind,version_id) VALUES(?,?,?,?,?)',
                               (row['id'], reading, now, 'absent', row['version_id']))
        return summary(db), fresh is not None


def connect_readonly(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=10)
    db.row_factory = sqlite3.Row
    return db


def main():
    parser = argparse.ArgumentParser(description='Consultar y respaldar el histórico privado de Waze.')
    parser.add_argument('--database', default='/var/lib/waze-monitor/history.sqlite3')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('status')
    backup = sub.add_parser('backup')
    backup.add_argument('--output', required=True)
    timeline = sub.add_parser('timeline')
    timeline.add_argument('report_id')
    timeline.add_argument('--after', type=int, default=0, help='Cursor de transición para paginar')
    args = parser.parse_args()
    db = connect_readonly(args.database)
    try:
        if args.command == 'status':
            result = summary(db)
            result['stale'] = not result['lastSuccess'] or time.time() - result['lastSuccess'] > 360
            print(json.dumps(result, ensure_ascii=False))
        elif args.command == 'timeline':
            rows = db.execute('''SELECT t.id,t.at,t.kind,r.source_at,v.body FROM transitions t
                JOIN versions v ON v.id=t.version_id LEFT JOIN readings r ON r.id=t.reading_id
                WHERE t.report_id=? AND t.id>? ORDER BY t.id LIMIT 500''', (args.report_id, args.after))
            print(json.dumps([{**dict(row), 'body': json.loads(row['body'])} for row in rows], ensure_ascii=False))
        else:
            target = Path(args.output)
            fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
            try:
                with closing(sqlite3.connect(target)) as destination:
                    db.backup(destination)
                    if destination.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                        raise ValueError('Backup integrity check failed')
                print('Respaldo verificado')
            except Exception:
                target.unlink(missing_ok=True)
                raise
    finally:
        db.close()


if __name__ == '__main__':
    main()
