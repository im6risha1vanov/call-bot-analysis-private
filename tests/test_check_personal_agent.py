import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import check_personal_agent as checker


class ReadOnlyCheckTests(unittest.TestCase):
    def test_package_hash_and_modified_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'asset').write_text('known', encoding='utf-8')
            manifest = root / 'manifest.json'
            manifest.write_text(json.dumps({'asset': hashlib.sha256((root / 'asset').read_bytes()).hexdigest()}), encoding='utf-8')
            pin = hashlib.sha256(manifest.read_bytes()).hexdigest()
            with patch.object(checker, 'PROJECT', root):
                checker.check_package(manifest, pin)
                (root / 'asset').write_text('changed', encoding='utf-8')
                with self.assertRaises(RuntimeError):
                    checker.check_package(manifest, pin)
