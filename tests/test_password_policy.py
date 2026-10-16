import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app import password_matches
from manage import main, valid_password


class PasswordPolicyTests(unittest.TestCase):
    # Public test fixture only; never installed as a production credential.
    fixture = 'Ab3!cdef'

    def test_eight_characters_and_required_categories(self):
        self.assertTrue(valid_password(self.fixture))
        self.assertTrue(valid_password(self.fixture * 3))
        for value in ('Ab3!cde', 'ab3!cdef', 'AB3!CDEF', 'Abc!defg', 'Ab3cdefg',
                      'Ab3 cdef', 'Ab3!cd\nf', self.fixture + 'x' * 1024):
            with self.subTest(value=value):
                self.assertFalse(valid_password(value))

    def invoke(self, target, passwords):
        with patch('sys.argv', ['manage.py', 'password', '--output', str(target)]), \
             patch('getpass.getpass', side_effect=passwords), \
             contextlib.redirect_stdout(io.StringIO()), \
             contextlib.redirect_stderr(io.StringIO()):
            main()

    def test_eight_character_password_creates_valid_hash(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / 'operator.hash'
            self.invoke(target, [self.fixture, self.fixture])
            encoded = target.read_text().strip()
            self.assertNotIn(self.fixture, encoded)
            self.assertTrue(password_matches(self.fixture, encoded))

    def test_rejection_does_not_create_a_credential(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / 'operator.hash'
            for passwords in (['Ab3!cde'], [self.fixture, 'Mismatch9!']):
                with self.assertRaises(SystemExit):
                    self.invoke(target, passwords)
                self.assertFalse(target.exists())

    def test_existing_credential_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / 'operator.hash'
            target.write_text('existing')
            with self.assertRaises(SystemExit):
                self.invoke(target, [self.fixture, self.fixture])
            self.assertEqual(target.read_text(), 'existing')
