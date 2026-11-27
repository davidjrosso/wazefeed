import json
from contextlib import closing
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from catalog import CITY
from collect_waze import collect
from monitoring import read_cache, RETENTION
from observation_store import connect_readonly
from test_monitoring import NOW, URL, alert, snapshot


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.secret = self.root / 'feed.url'
        self.secret.write_text(URL)
        self.cache = self.root / 'current.json'
        self.history = self.root / 'history.sqlite3'

    def poll(self, items=None, offset=0, source=None, failure=False):
        def fetch(_):
            if failure:
                raise RuntimeError(URL)
            return snapshot(items, NOW+offset if source is None else source)
        return collect(self.secret, self.cache, CITY, NOW+offset, fetch)

    def rows(self, sql, params=()):
        db = connect_readonly(self.history)
        try:
            return [dict(row) for row in db.execute(sql, params)]
        finally:
            db.close()

    def test_repeated_observations_are_not_new_reports(self):
        self.assertTrue(self.poll())
        self.assertTrue(self.poll(offset=120, source=NOW))
        self.assertTrue(self.poll(offset=240))
        report, = self.rows('SELECT * FROM reports')
        self.assertEqual(report['first_seen'], NOW)
        self.assertEqual(report['last_seen'], NOW+240)
        self.assertEqual(len(self.rows('SELECT * FROM versions')), 1)
        self.assertEqual(len(self.rows('SELECT * FROM sightings')), 3)
        self.assertEqual([r['kind'] for r in self.rows('SELECT kind FROM transitions')], ['new'])
        self.assertEqual([r['repeated_source'] for r in self.rows('SELECT repeated_source FROM readings')], [0, 1, 0])
        self.assertNotIn(URL, self.history.read_bytes().decode(errors='ignore'))

    def test_change_absence_return_and_publication_are_distinct(self):
        self.poll()
        self.poll([alert(confidence=3)], offset=120)
        self.poll([], offset=240)
        self.poll([alert(confidence=3)], offset=360)
        self.assertEqual([r['kind'] for r in self.rows('SELECT kind FROM transitions ORDER BY id')],
                         ['new', 'changed', 'absent', 'reappeared'])
        report, = self.rows('SELECT * FROM reports')
        self.assertEqual(report['published_at'], NOW-100)
        self.assertEqual(report['first_seen'], NOW)
        self.assertEqual(report['last_seen'], NOW+360)
        self.assertEqual(len(self.rows('SELECT * FROM versions')), 2)
        self.assertEqual(self.rows('PRAGMA foreign_key_check'), [])

    def test_failure_and_partial_reads_do_not_mark_absent(self):
        self.poll()
        self.assertFalse(self.poll(offset=120, failure=True))
        self.assertTrue(self.poll([alert('bad', location={})], offset=240))
        self.assertEqual(self.rows('SELECT present,last_seen FROM reports'), [{'present': 1, 'last_seen': NOW}])
        self.assertEqual([r['status'] for r in self.rows('SELECT status FROM readings')], ['ok', 'failed', 'partial'])
        self.assertEqual(len(self.rows('SELECT * FROM sightings')), 1)
        self.assertEqual(len(self.rows('SELECT * FROM transitions')), 1)
        self.assertNotIn(URL, self.history.read_bytes().decode(errors='ignore'))

    def test_invalid_envelope_and_stale_source_are_recorded(self):
        self.poll()
        self.assertFalse(collect(self.secret, self.cache, CITY, NOW+120, lambda _: {}))
        self.assertFalse(self.poll(offset=1000, source=NOW))
        self.assertEqual(self.rows('SELECT present FROM reports'), [{'present': 1}])
        self.assertEqual(len(self.rows("SELECT * FROM readings WHERE status='failed'")), 2)

    def test_archive_guards_source_order_even_if_cache_is_lost(self):
        self.poll()
        self.cache.unlink()
        self.assertFalse(self.poll(offset=120, source=NOW-1))
        self.assertEqual(self.rows('SELECT present FROM reports'), [{'present': 1}])
        self.assertEqual(read_cache(self.cache)['meta']['error'], 'invalid_feed')

    def test_archive_survives_cache_retention_and_restart(self):
        self.poll()
        self.poll([], offset=RETENTION+1)
        self.assertEqual(read_cache(self.cache)['alerts'], [])
        self.assertEqual(len(self.rows('SELECT * FROM reports')), 1)
        self.poll(offset=RETENTION+121)
        report, = self.rows('SELECT * FROM reports')
        self.assertEqual(report['first_seen'], NOW)
        self.assertEqual(report['present'], 1)
        self.assertEqual(self.rows('SELECT kind FROM transitions ORDER BY id DESC LIMIT 1')[0]['kind'], 'reappeared')

    def test_import_existing_cache_is_explicit_and_only_once(self):
        from monitoring import merge
        self.cache.write_text(json.dumps(merge({}, snapshot(), CITY['bounds'], NOW)))
        self.poll(offset=120)
        self.poll(offset=240)
        report, = self.rows('SELECT * FROM reports')
        self.assertEqual(report['imported'], 1)
        self.assertEqual(report['first_seen'], NOW)
        self.assertEqual(self.rows('SELECT kind FROM transitions'), [{'kind': 'imported'}])
        self.assertEqual(len(self.rows('SELECT * FROM sightings')), 2)

    def test_database_error_keeps_cache_and_reports_failure(self):
        self.poll()
        with patch('collect_waze.record', side_effect=sqlite3.OperationalError('disk full')):
            self.assertFalse(self.poll([], offset=120))
        data = read_cache(self.cache)
        self.assertTrue(data['alerts'][0]['present'])
        self.assertEqual(data['meta']['lastSuccess'], NOW)
        self.assertEqual(data['meta']['error'], 'archive_failed')

    def test_transaction_rolls_back_on_interrupted_write(self):
        self.poll()
        with patch('observation_store.version_for', side_effect=sqlite3.OperationalError('disk full')):
            self.assertFalse(self.poll([alert(confidence=4)], offset=120))
        self.assertEqual(len(self.rows('SELECT * FROM readings')), 1)
        self.assertEqual(self.rows('SELECT last_seen FROM reports'), [{'last_seen': NOW}])
        self.assertTrue(self.poll([alert(confidence=4)], offset=240))

    def test_city_area_change_is_recorded_without_disappearance(self):
        self.poll()
        new_city = {**CITY, 'bounds': [0, 0, 1, 1]}
        self.assertFalse(collect(self.secret, self.cache, new_city, NOW+120, lambda _: snapshot([], NOW+120)))
        self.assertEqual(self.rows('SELECT present FROM reports'), [{'present': 1}])
        self.assertEqual(self.rows('SELECT error FROM readings ORDER BY id DESC LIMIT 1'), [{'error': 'area_changed'}])

    def test_outside_area_and_duplicates_counted_without_inflating_reports(self):
        self.poll([alert(), alert(), alert('elsewhere', location={'x': 0, 'y': 0})])
        self.assertEqual(len(self.rows('SELECT * FROM reports')), 1)
        self.assertEqual(self.rows('SELECT invalid_count,outside_count,status FROM readings'),
                         [{'invalid_count': 1, 'outside_count': 1, 'status': 'partial'}])

    def test_cache_write_failure_does_not_lose_committed_archive(self):
        with patch('collect_waze.atomic', side_effect=OSError('disk error')):
            with self.assertRaises(OSError):
                self.poll()
        self.assertEqual(len(self.rows('SELECT * FROM reports')), 1)
        self.assertTrue(self.poll(offset=120))
        self.assertEqual(self.rows('SELECT first_seen FROM reports'), [{'first_seen': NOW}])

    def test_cli_backup_integrity_and_refusal_to_overwrite(self):
        self.poll()
        backup = self.root / 'backup.sqlite3'
        cmd = [sys.executable, 'observation_store.py', '--database', str(self.history)]
        run = subprocess.run(cmd+['backup', '--output', str(backup)], capture_output=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        with closing(sqlite3.connect(backup)) as db:
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(db.execute('SELECT count(*) FROM reports').fetchone()[0], 1)
        self.assertNotEqual(subprocess.run(cmd+['backup', '--output', str(backup)], capture_output=True).returncode, 0)
        status = subprocess.run(cmd+['status'], capture_output=True, check=True)
        self.assertEqual(json.loads(status.stdout)['reports'], 1)
        timeline = subprocess.run(cmd+['timeline', 'sample-1'], capture_output=True, check=True)
        self.assertEqual(json.loads(timeline.stdout)[0]['kind'], 'new')

    def test_cache_cannot_be_used_as_database(self):
        with self.assertRaises(ValueError):
            collect(self.secret, self.cache, CITY, NOW, lambda _: snapshot(), history=self.cache)
