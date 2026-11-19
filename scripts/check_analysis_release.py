#!/usr/bin/env python3
"""Reject incompatible analysis releases before changing application files or services.

Install this file outside the checkout at
/opt/callbot-astra-releases/check_analysis_release.py. Candidate code must not
supply its own expected hashes. No production database or paid API is used.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

BASELINE = '52e4d575c03788d09194346362b1e06ef4476feb'
PACKAGE_HASH = '484433874b7d7a83f8c87e8daebd8fecad72ce26c01d50a85e7ffb446f594380'
ORIGINAL = {
    'analysis.py': 'eee685f1bc0a31f8301ffef332a703d5d0569f7eff97b5073dc8f1b545d28951',
    'prompt_score.md': '3a8e17491b0860e8ab6dd32001af4feeee9d91ca5a649bb1eca65afd88f160b4',
    'prompt_short.md': 'f98bb2df92885817a07d15ac7c9127a8fb2377a00859b11d4b87a1afa061f139',
    'prompt_review.md': 'fad4cda6e2d37f836fdc74e1ba1f6f81dc88ce24da15dd31eb5639b741f212d1',
}
CORE = ('astra_worker.py', 'queue_runner.py', 'mango_client.py',
        'handlers/analyze_call.py', 'unified/analysis_policy.py', 'comparison/registry.py')

# Runs with synthetic settings in a fresh interpreter; socket connections and
# child processes are forbidden even if an import accidentally tries to use them.
SMOKE = r'''
import asyncio, json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
def offline(event, args):
    if event in {'socket.connect', 'socket.connect_ex', 'socket.getaddrinfo', 'subprocess.Popen'}:
        raise RuntimeError('Network and subprocesses are disabled in release checks')
sys.addaudithook(offline)
import analysis
assert callable(analysis.score_call) and callable(analysis.short_report_call)
if sys.argv[2] == 'legacy':
    raise SystemExit(0)
import astra_worker
import handlers.analyze_call
import queue_runner
from unified import analysis_policy as policy
assert queue_runner._REGISTRY['analyze_call'] is handlers.analyze_call.analyze_call
assert policy.verify_original()['sha256'] == sys.argv[3]
assert policy.current_configuration()['package_sha256'] == sys.argv[3]
assert policy.legacy_configuration()['package_sha256'] == sys.argv[3]
class Pool:
    def __init__(self, legacy=False): self.legacy, self.inserts = legacy, []
    def acquire(self): return self
    def transaction(self): return self
    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass
    async def fetchval(self, *args): return True
    async def fetchrow(self, *args):
        return {'active':'legacy','saved_configuration':{}} if self.legacy else None
    async def execute(self, sql, *args):
        if sql.startswith('INSERT INTO tasks'): self.inserts.append((sql, args))
async def check():
    for legacy in (False, True):
        pool = Pool(legacy)
        await astra_worker._enqueue_analyze_call(pool, 17, 123, 'release-check-only')
        assert len(pool.inserts) == 1
        sql, args = pool.inserts[0]
        assert 'ON CONFLICT' in sql
        assert args[0] == 17 and args[2] == 'release-check-only'
        data = json.loads(args[1])
        assert data['call_id'] == 123
        assert data['analysis_package']['package_sha256'] == sys.argv[3]
    await astra_worker.bot.session.close()
    await astra_worker.mango_client.close()
asyncio.run(check())
'''


def file_at(root, name):
    relative = Path(name)
    if relative.is_absolute() or '..' in relative.parts or not relative.parts:
        raise RuntimeError('Invalid release file path')
    path = root / relative
    if any(part.is_symlink() for part in [path, *path.parents] if part != root.parent):
        raise RuntimeError('Symlink in release file: ' + name)
    if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        raise RuntimeError('Missing release file: ' + name)
    return path


def check_tree(root, *, legacy=False):
    root = Path(root).resolve()
    for name, digest in ORIGINAL.items():
        if hashlib.sha256(file_at(root, name).read_bytes()).hexdigest() != digest:
            raise RuntimeError('Original analysis package changed: ' + name)
    if not legacy:
        manifest = json.loads(file_at(root, 'comparison/packages/manifest.json').read_text())
        expected = {'legacy/' + name: digest for name, digest in ORIGINAL.items()}
        expected['legacy/__init__.py'] = hashlib.sha256(b'').hexdigest()
        package = manifest.get('legacy_52e4d575', {})
        if (package.get('source_commit') != BASELINE or package.get('files') != expected
                or package.get('sha256') != PACKAGE_HASH):
            raise RuntimeError('Original package manifest differs from trusted baseline')
        for package in manifest.values():
            files = package['files']
            digest = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
            if digest != package['sha256']:
                raise RuntimeError('Invalid comparison package manifest')
            for name, digest in files.items():
                path = file_at(root, 'comparison/packages/' + name)
                if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                    raise RuntimeError('Comparison package changed: ' + name)
    for name in ('analysis.py',) + (() if legacy else CORE):
        compile(file_at(root, name).read_bytes(), name, 'exec')


def smoke(root, *, legacy=False):
    env = {k: os.environ[k] for k in ('PATH', 'SYSTEMROOT', 'LANG', 'TZ') if k in os.environ}
    env.update(PYTHONDONTWRITEBYTECODE='1', CVC_API_KEY='release-check-only',
               BOT_TOKEN='123456:release_check_only', TRAIN_BOT_TOKEN='123457:release_check_only',
               ANTHROPIC_API_KEY='release-check-only', DEEPGRAM_API_KEY='release-check-only',
               ENCRYPTION_KEY='MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=')
    try:
        result = subprocess.run([sys.executable, '-B', '-c', SMOKE, str(root),
                                 'legacy' if legacy else 'current', PACKAGE_HASH],
                                cwd=root, env=env, capture_output=True, timeout=45)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError('Analysis enqueue/import check timed out') from exc
    if result.returncode:
        # Imports cannot leak provider bodies, credentials or customer data.
        raise RuntimeError('Analysis enqueue/import check failed; candidate was not installed')


def export_commit(root, ref, dest):
    if not re.fullmatch(r'[a-f0-9]{40}', ref):
        raise RuntimeError('Use a full pinned commit ID')
    with tempfile.TemporaryFile() as stream:
        result = subprocess.run(['git', '-C', str(root), 'archive', '--format=tar', ref],
                                stdout=stream, stderr=subprocess.PIPE)
        if result.returncode:
            raise RuntimeError('Cannot export candidate commit')
        stream.seek(0)
        with tarfile.open(fileobj=stream) as archive:
            archive.extractall(dest, filter='data')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('/opt/callbot-astra'))
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--commit')
    group.add_argument('--overlay', type=Path)
    parser.add_argument('--files', nargs='+')
    args = parser.parse_args(argv)
    if bool(args.overlay) != bool(args.files):
        parser.error('--overlay and --files must be supplied together')
    root = args.root.resolve()
    with tempfile.TemporaryDirectory(prefix='averon-analysis-preflight-') as directory:
        candidate = Path(directory)
        if args.commit:
            export_commit(root, args.commit, candidate)
        elif args.overlay:
            head = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD']).decode().strip()
            export_commit(root, head, candidate)
            for name in args.files:
                source = file_at(args.overlay.resolve(), name)
                target = candidate / name
                if target.is_symlink() or not target.resolve().is_relative_to(candidate):
                    raise RuntimeError('Invalid overlay destination')
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
        else:
            # Check the actual working files, not just HEAD. Only tracked source
            # is copied; .env, recordings and private state never enter the probe.
            names = subprocess.check_output(['git', '-C', str(root), 'ls-files', '-z']).split(b'\0')
            for raw in names:
                if not raw:
                    continue
                name = raw.decode()
                if name == '.env' or name.startswith(('.env.', 'recordings/', 'crm_private/')):
                    raise RuntimeError('Private file is unexpectedly tracked')
                source = file_at(root, name)
                target = candidate / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
        legacy = args.commit == BASELINE
        check_tree(candidate, legacy=legacy)
        smoke(candidate, legacy=legacy)
    print(json.dumps({'analysis_preflight':'ok','package_sha256':PACKAGE_HASH,
                      'database':'not used','network':'disabled'}))


if __name__ == '__main__':
    try:
        main()
    except (OSError, RuntimeError, ValueError, SyntaxError, KeyError) as exc:
        print('BLOCKED: ' + (str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__), file=sys.stderr)
        sys.exit(1)
