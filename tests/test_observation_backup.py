from contextlib import closing
import gzip
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from backup_observations import backup


class BackupTests(unittest.TestCase):
    def test_compressed_backup_is_restorable_and_only_old_copies_are_pruned(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'history.sqlite3'
            with closing(sqlite3.connect(source)) as db:
                db.execute('CREATE TABLE sample (id INTEGER PRIMARY KEY)')
                db.execute('INSERT INTO sample VALUES(42)')
                db.commit()
            directory = root / 'backups'
            backup(source, directory, keep=2)
            unrelated = directory / 'manual.sqlite3.gz'
            unrelated.write_bytes(b'preserve')
            backup(source, directory, keep=2)
            latest = backup(source, directory, keep=2)
            self.assertEqual(len(list(directory.glob('history-*.sqlite3.gz'))), 2)
            self.assertTrue(unrelated.exists())
            restored = root / 'restored.sqlite3'
            restored.write_bytes(gzip.decompress(latest.read_bytes()))
            with closing(sqlite3.connect(restored)) as db:
                self.assertEqual(db.execute('SELECT id FROM sample').fetchone()[0], 42)
                self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            existing = set(directory.iterdir())
            with patch('backup_observations.gzip.open', side_effect=OSError('disk full')):
                with self.assertRaises(OSError):
                    backup(source, directory, keep=1)
            self.assertEqual(set(directory.iterdir()), existing)
            self.assertTrue(source.exists())

    def test_missing_database_is_not_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'missing.sqlite3'
            with self.assertRaises(sqlite3.OperationalError):
                backup(source, Path(tmp) / 'backups')
            self.assertFalse(source.exists())
