#!/usr/bin/env python3
"""Install the reviewed preflight outside Git; preserve existing release controllers."""
import argparse
import ast
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys

PROJECT = Path('/opt/callbot-astra')
STATE = Path('/opt/callbot-astra-releases')
MARKER = '# analysis-release-preflight: before any database or service mutation'


def add_preflight(text):
    tree = ast.parse(text)
    switch = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name=='switch')
    fragment = '\n'.join(text.splitlines()[switch.lineno-1:switch.end_lineno])
    if MARKER in fragment:
        return text
    current = next(node for node in switch.body if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Name) and target.id=='current' for target in node.targets))
    lines = text.splitlines(keepends=True)
    lines.insert(current.end_lineno, '    '+MARKER+'\n'
        "    command([sys.executable, '/opt/callbot-astra-releases/check_analysis_release.py',\n"
        "             '--root', str(PROJECT), '--commit', target])\n")
    result = ''.join(lines)
    compile(result, '<release-controller>', 'exec')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if os.geteuid()!=0 or socket.gethostname().split('.')[0]!='9015421-nt422325':
        raise RuntimeError('Wrong host or user')
    source = Path(__file__).with_name('check_analysis_release.py')
    destination = STATE / source.name
    changes = {destination: source.read_bytes()}
    for name in ('release_manager.py','release_manager-crm-699c50b.py'):
        path = STATE/name
        if path.is_file():
            changes[path] = add_preflight(path.read_text()).encode()
    if any(path.is_symlink() for path in changes):
        raise RuntimeError('Unexpected symlink in installed controllers')
    subprocess.run([sys.executable, '-B', str(source), '--root', str(PROJECT)], check=True)
    if not args.apply:
        print(json.dumps({'checked': [str(p) for p in changes], 'changed':False}))
        return
    import fcntl
    with (STATE/'switch.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        backup = STATE / ('analysis-guard-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
        backup.mkdir(mode=0o700)
        previous = {}
        for path in changes:
            previous[path] = (path.read_bytes(),path.stat().st_mode & 0o777) if path.exists() else None
            if path.exists():
                shutil.copy2(path, backup/path.name)
        try:
            for path, data in changes.items():
                pending = path.with_name('.'+path.name+'.analysis-guard-tmp')
                pending.write_bytes(data)
                pending.chmod(previous[path][1] if previous[path] else 0o644)
                pending.replace(path)
        except BaseException:
            for path, old in previous.items():
                if old is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_bytes(old[0]); path.chmod(old[1])
            raise
    print(json.dumps({'installed':[str(p) for p in changes], 'backup':str(backup),
                      'service_restarts':0}))


if __name__=='__main__':
    main()
