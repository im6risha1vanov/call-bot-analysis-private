"""Real temporary candidate trees and Git commits; no production database/API."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts import check_analysis_release as guard
from scripts.install_analysis_release_guard import add_preflight, MARKER

ROOT = Path(__file__).resolve().parents[1]


class CandidateTests(unittest.TestCase):
    def setUp(self):
        self.temp = self.enterContext(tempfile.TemporaryDirectory(prefix='analysis-guard-test-'))
        self.root = Path(self.temp) / 'candidate'
        self.root.mkdir()
        for name in (*guard.ORIGINAL, *guard.CORE):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT/name, path)
        shutil.copytree(ROOT/'comparison/packages', self.root/'comparison/packages')

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.root), *args], stderr=subprocess.DEVNULL).decode().strip()

    def commit(self):
        self.git('add', '.')
        self.git('-c','user.name=Fixture','-c','user.email=fixture@invalid','commit','-m','candidate')
        return self.git('rev-parse','HEAD')

    def init(self):
        self.git('init', '-b', 'main')
        return self.commit()

    def test_current_original_package_is_accepted(self):
        guard.check_tree(self.root)

    def test_original_code_and_every_prompt_are_pinned(self):
        for name in guard.ORIGINAL:
            with self.subTest(name=name):
                path = self.root/name
                original = path.read_bytes()
                path.write_bytes(original+b'\nchanged')
                with self.assertRaisesRegex(RuntimeError, 'Original analysis package changed'):
                    guard.check_tree(self.root)
                path.write_bytes(original)

    def test_rewriting_both_snapshot_and_manifest_cannot_repin_the_original(self):
        import hashlib
        data = (self.root/'analysis.py').read_bytes()+b'\n# changed\n'
        (self.root/'analysis.py').write_bytes(data)
        (self.root/'comparison/packages/legacy/analysis.py').write_bytes(data)
        path = self.root/'comparison/packages/manifest.json'
        manifest = json.loads(path.read_text())
        package = manifest['legacy_52e4d575']
        package['files']['legacy/analysis.py'] = hashlib.sha256(data).hexdigest()
        package['sha256'] = hashlib.sha256(json.dumps(package['files'],sort_keys=True).encode()).hexdigest()
        path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(RuntimeError, 'Original analysis package changed'):
            guard.check_tree(self.root)

    def test_manifest_and_course_files_are_checked(self):
        path = self.root/'comparison/packages/manifest.json'
        original = path.read_bytes()
        manifest = json.loads(original)
        manifest['legacy_52e4d575']['source_commit'] = '0'*40
        path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(RuntimeError, 'trusted baseline'):
            guard.check_tree(self.root)
        path.write_bytes(original)
        (self.root/'comparison/packages/course/prompts/course/score.md').write_text('changed')
        with self.assertRaisesRegex(RuntimeError, 'Comparison package changed'):
            guard.check_tree(self.root)

    def test_core_syntax_and_missing_handler_are_rejected(self):
        path = self.root/'handlers/analyze_call.py'
        original = path.read_bytes()
        path.write_text('def invalid(:\n')
        with self.assertRaises(SyntaxError):
            guard.check_tree(self.root)
        path.write_bytes(original)
        path.unlink()
        with self.assertRaisesRegex(RuntimeError, 'Missing release file'):
            guard.check_tree(self.root)

    @unittest.skipIf(__import__('os').name=='nt', 'symlinks tested on deployment host')
    def test_symlinks_cannot_redirect_protected_files(self):
        path = self.root/'analysis.py'
        saved = self.root/'other.py'
        path.rename(saved)
        path.symlink_to(saved)
        with self.assertRaisesRegex(RuntimeError, 'Symlink'):
            guard.check_tree(self.root)

    def test_bad_commit_is_rejected_without_checkout_or_service_changes(self):
        good = self.init()
        (self.root/'analysis.py').write_bytes((self.root/'analysis.py').read_bytes()+b'\n# regression\n')
        bad = self.commit()
        self.git('checkout',good)
        before = (self.root/'analysis.py').read_bytes()
        with patch.object(guard,'smoke') as smoke:
            with self.assertRaisesRegex(RuntimeError,'Original analysis package changed'):
                guard.main(['--root',str(self.root),'--commit',bad])
        smoke.assert_not_called()
        self.assertEqual(self.git('rev-parse','HEAD'),good)
        self.assertEqual((self.root/'analysis.py').read_bytes(),before)
        self.assertEqual(self.git('status','--porcelain'),'')

    def test_commit_check_reads_the_candidate_instead_of_dirty_working_files(self):
        good = self.init()
        (self.root/'analysis.py').write_text('dirty live source')
        with patch.object(guard,'smoke') as smoke, patch('builtins.print'):
            guard.main(['--root',str(self.root),'--commit',good])
        smoke.assert_called_once()
        self.assertEqual((self.root/'analysis.py').read_text(),'dirty live source')

    def test_overlay_is_validated_without_touching_the_application(self):
        self.init()
        overlay = Path(self.temp)/'overlay'
        overlay.mkdir()
        (overlay/'analysis.py').write_text('invalid proposed source')
        before = (self.root/'analysis.py').read_bytes()
        with patch.object(guard,'smoke') as smoke:
            with self.assertRaisesRegex(RuntimeError,'Original analysis package changed'):
                guard.main(['--root',str(self.root),'--overlay',str(overlay),'--files','analysis.py'])
        smoke.assert_not_called()
        self.assertEqual((self.root/'analysis.py').read_bytes(),before)

    def test_working_tree_check_detects_uncommitted_regression(self):
        self.init()
        (self.root/'analysis.py').write_text('uncommitted invalid source')
        with self.assertRaisesRegex(RuntimeError,'Original analysis package changed'):
            guard.main(['--root',str(self.root)])


class RuntimeTests(unittest.TestCase):
    def test_real_poll_enqueue_and_analysis_import_with_synthetic_settings(self):
        guard.check_tree(ROOT)
        guard.smoke(ROOT)

    def test_runtime_probe_cannot_connect_to_network_or_database(self):
        code = guard.SMOKE.replace('import analysis',
            "import socket; socket.create_connection(('127.0.0.1', 5432))\nimport analysis",1)
        with patch.object(guard,'SMOKE',code):
            with self.assertRaisesRegex(RuntimeError,'enqueue/import check failed'):
                guard.smoke(ROOT)


class InstallerTests(unittest.TestCase):
    def test_preserves_controller_and_checks_before_database_migration_or_stop(self):
        text = '''import sys
def switch(target, *, rollback=False):
    commit(target)
    current=command(['git','rev-parse','HEAD'])
    busy=database('status')
    command(['systemctl','stop'])
'''
        updated = add_preflight(text)
        self.assertIn(text.splitlines()[-1],updated)
        self.assertIn("busy=database('status')",updated)
        self.assertLess(updated.index(MARKER),updated.index("database('status')"))
        self.assertEqual(add_preflight(updated),updated)

    def test_runtime_failure_blocks_a_compatible_hash_package(self):
        code = guard.SMOKE.replace("assert policy.verify_original()", "raise RuntimeError('simulated queue incompatibility')\nassert policy.verify_original()",1)
        with patch.object(guard,'SMOKE',code):
            with self.assertRaisesRegex(RuntimeError,'enqueue/import check failed'):
                guard.smoke(ROOT)


if __name__=='__main__':
    unittest.main()
