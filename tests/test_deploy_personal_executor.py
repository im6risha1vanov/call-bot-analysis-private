"""Offline checks for release package integrity; never contacts production."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import deploy_personal_executor as deploy


class PackageValidationTests(unittest.TestCase):
    def test_manifest_and_contents_are_pinned(self):
        with tempfile.TemporaryDirectory() as tmp:
            package = Path(tmp)
            entries = {}
            for name in deploy.FILES:
                path = package / 'files' / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(name, encoding='utf-8')
                entries[name] = hashlib.sha256(path.read_bytes()).hexdigest()
            manifest = package / 'manifest.json'
            manifest.write_text(json.dumps(entries), encoding='utf-8')
            pin = hashlib.sha256(manifest.read_bytes()).hexdigest()
            self.assertEqual(deploy.validate_package(package, pin), entries)
            (package / 'files' / deploy.FILES[0]).write_text('changed', encoding='utf-8')
            with self.assertRaises(RuntimeError):
                deploy.validate_package(package, pin)
            with self.assertRaises(RuntimeError):
                deploy.validate_package(package, '0' * 64)

    def test_health_requires_both_loops_and_no_errors(self):
        with patch.object(deploy, 'command', return_value='a' * 32), \
             patch.object(deploy, 'journal_has', side_effect=lambda invocation, marker: marker in {
                 'Telegram getUpdates succeeded over IPv4', 'Personal reminder loop started successfully', 'Personal executor loop started'}):
            self.assertTrue(deploy.polling_ready())
        with patch.object(deploy, 'command', return_value='a' * 32), \
             patch.object(deploy, 'journal_has', side_effect=lambda invocation, marker: marker != 'Personal executor loop started'):
            self.assertFalse(deploy.polling_ready())
        with patch.object(deploy, 'command', return_value='a' * 32), \
             patch.object(deploy, 'journal_has', return_value=True):
            self.assertFalse(deploy.polling_ready())
