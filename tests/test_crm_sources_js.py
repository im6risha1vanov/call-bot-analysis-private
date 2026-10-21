"""«Источники»: логика подборщика и закладка «Собрать номера» проверяются в Node (tests/js/sources_test.js)."""
import shutil
import subprocess
import unittest
from pathlib import Path

NODE = shutil.which('node')


@unittest.skipUnless(NODE, 'node не установлен')
class SourcesJsTests(unittest.TestCase):
    def test_picker_and_bookmarklet(self):
        root = Path(__file__).resolve().parents[1]
        r = subprocess.run([NODE, str(root / 'tests/js/sources_test.js')], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout.strip(), 'ok')
