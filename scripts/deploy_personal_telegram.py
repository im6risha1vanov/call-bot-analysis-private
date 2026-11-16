#!/usr/bin/env python3
"""Apply a reviewed, pinned personal-agent package. Never runs without --apply.

Package is a directory with manifest.json and files/; pin its manifest SHA-256
on the command line independently after review. Run with the CRM venv Python.
"""
import argparse
import asyncio
import hashlib
import http.client
import json
import os
from pathlib import Path
import shutil
import socket
import stat
import subprocess
import sys
import time
from datetime import datetime, timezone

PROJECT = Path('/opt/callbot-astra')
BASE = '0ea2515ce9f6c196238042f15a96813b31467c47'
PYTHON = '/opt/averon-crm-venvs/699c50b/bin/python'
DROPIN = Path('/etc/systemd/system/callbot-astra.service.d/personal-agent.conf')
BACKUPS = Path('/var/backups/callbot-personal-telegram')
FILES = ('crm/personal.py', 'crm/static/index.html', 'crm/static/personal.js', 'crm/static/presentation.js',
         'demo_bot.py', 'personal_telegram.py', 'review/prompt_ui.py',
         'migrations/046_crm_personal_telegram.sql', 'scripts/crm_setup.sh')
DROPIN_CONTENT = '[Service]\nEnvironment="CRM_PERSONAL_PILOT_TELEGRAM_IDS=841868875,304327068"\n'


def command(*args):
    result = subprocess.run(args, cwd=PROJECT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode:
        raise RuntimeError(f'{args[0]} failed ({result.returncode})')
    return result.stdout.strip()


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_package(package, expected):
    manifest = package / 'manifest.json'
    if not expected or len(expected) != 64 or digest(manifest) != expected.lower():
        raise RuntimeError('Package manifest digest mismatch')
    entries = json.loads(manifest.read_text(encoding='utf-8'))
    if set(entries) != set(FILES):
        raise RuntimeError('Unexpected package files')
    for name in FILES:
        path = package / 'files' / name
        if path.is_symlink() or not path.is_file() or digest(path) != entries[name]:
            raise RuntimeError('Package file mismatch: ' + name)
    return entries


async def migrate(sql):
    import asyncpg
    from dotenv import dotenv_values
    dsn = dotenv_values(PROJECT / '.env').get('DATABASE_URL')
    if not dsn:
        raise RuntimeError('DATABASE_URL missing')
    conn = await asyncpg.connect(dsn)
    try:
        if await conn.fetchval('SELECT current_database()') != 'callbot':
            raise RuntimeError('Unexpected database')
        async with conn.transaction():
            await conn.execute(sql)
    finally:
        await conn.close()


def status(path):
    conn = http.client.HTTPConnection('127.0.0.1', 8095, timeout=3)
    try:
        conn.request('GET', path)
        response = conn.getresponse(); response.read()
        return response.status
    finally:
        conn.close()


OTHER_SERVICES = ('callbot-astra-trainer.service', 'callbot-astra-worker.service', 'callbot-astra-queue-runner.service')


def polling_ready():
    invocation = command('systemctl', 'show', 'callbot-astra.service', '-p', 'InvocationID', '--value')
    if not invocation or len(invocation) != 32 or not all(c in '0123456789abcdef' for c in invocation):
        return False
    return bool(command('journalctl', '_SYSTEMD_INVOCATION_ID=' + invocation, '--grep=Telegram getUpdates succeeded over IPv4', '-o', 'cat', '--no-pager'))


def check_health():
    end = time.monotonic() + 60
    while time.monotonic() < end:
        try:
            if command('systemctl', 'is-active', 'callbot-crm.service') == 'active' and command('systemctl', 'is-active', 'callbot-astra.service') == 'active' and all(command('systemctl', 'is-active', service) == 'active' for service in OTHER_SERVICES) and status('/') == 200 and status('/api/personal/profile') == 401 and polling_ready():
                return
        except (OSError, RuntimeError, http.client.HTTPException):
            pass
        time.sleep(2)
    raise RuntimeError('CRM health check failed')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--package', required=True, type=Path)
    parser.add_argument('--manifest-sha256', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if not args.apply:
        raise RuntimeError('Explicit --apply is required')
    if os.geteuid() != 0 or socket.gethostname().split('.')[0] != '9015421-nt422325' or PROJECT.resolve() != Path('/opt/callbot-astra'):
        raise RuntimeError('Wrong host, directory, or user')
    if Path(sys.executable).absolute() != Path(PYTHON):
        raise RuntimeError('Use the pinned CRM Python')
    import fcntl
    lock = open('/run/lock/callbot-personal-agent-deploy.lock', 'a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise RuntimeError('Another personal-agent deployment is running')
    package = args.package.resolve()
    validate_package(package, args.manifest_sha256)
    if command('git', 'rev-parse', 'HEAD') != BASE or command('git', 'status', '--porcelain', '--untracked-files=all'):
        raise RuntimeError('Production checkout differs from clean baseline')
    working = command('systemctl', 'show', 'callbot-crm.service', '-p', 'WorkingDirectory', '--value')
    execution = command('systemctl', 'show', 'callbot-crm.service', '-p', 'ExecStart', '--value')
    if working != str(PROJECT) or f'path={PYTHON} ' not in execution or f'argv[]={PYTHON} -m crm.web ' not in execution:
        raise RuntimeError('CRM service does not match the pinned application and command')
    bot_working = command('systemctl', 'show', 'callbot-astra.service', '-p', 'WorkingDirectory', '--value')
    bot_exec = command('systemctl', 'show', 'callbot-astra.service', '-p', 'ExecStart', '--value')
    if bot_working != str(PROJECT) or f'path={PYTHON} ' not in bot_exec or f'argv[]={PYTHON} {PROJECT}/demo_bot.py ' not in bot_exec:
        raise RuntimeError('Bot service does not match pinned command')
    if any(command('systemctl', 'is-active', service) != 'active' for service in OTHER_SERVICES):
        raise RuntimeError('A required neighboring service is not active')
    if command('systemctl', 'is-active', 'callbot-astra.service') != 'active':
        raise RuntimeError('Bot is not active before deployment')
    if command('systemctl', 'is-active', 'callbot-crm.service') != 'active':
        raise RuntimeError('CRM is not active before deployment')
    for name in FILES:
        dest = PROJECT / name
        if dest.is_symlink() or (dest.exists() and not dest.is_file()):
            raise RuntimeError('Unexpected destination: ' + name)
    if DROPIN.is_symlink() or (DROPIN.exists() and not DROPIN.is_file()):
        raise RuntimeError('Unexpected drop-in')
    BACKUPS.mkdir(mode=0o700, parents=True, exist_ok=True)
    backup = BACKUPS / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backup.mkdir(mode=0o700)
    existed = []
    for name in FILES:
        src = PROJECT / name
        if src.exists():
            target = backup / 'files' / name; target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            shutil.copy2(src, target); existed.append(name)
    shutil.copy2(PROJECT / '.env', backup / 'env'); os.chmod(backup / 'env', 0o600)
    had_dropin = DROPIN.exists()
    if had_dropin:
        shutil.copy2(DROPIN, backup / 'dropin')
    (backup / 'state.json').write_text(json.dumps({'existed': existed, 'dropin': had_dropin}), encoding='utf-8')
    if command('git', 'rev-parse', 'HEAD') != BASE or command('git', 'status', '--porcelain', '--untracked-files=all'):
        raise RuntimeError('Production checkout changed during preflight')
    validate_package(package, args.manifest_sha256)
    try:
        asyncio.run(migrate((package / 'files/migrations/046_crm_personal_telegram.sql').read_text(encoding='utf-8')))
        for name in FILES:
            target = PROJECT / name; target.parent.mkdir(parents=True, exist_ok=True)
            mode = stat.S_IMODE(target.stat().st_mode) if target.exists() else 0o644
            shutil.copy2(package / 'files' / name, target)
            os.chmod(target, mode)
        DROPIN.parent.mkdir(parents=True, exist_ok=True)
        DROPIN.write_text(DROPIN_CONTENT, encoding='utf-8'); os.chmod(DROPIN, 0o644)
        command('systemctl', 'daemon-reload')
        command('systemctl', 'restart', 'callbot-crm.service')
        command('systemctl', 'restart', 'callbot-astra.service')
        check_health()
    except Exception:
        for name in FILES:
            target = PROJECT / name
            if name in existed:
                shutil.copy2(backup / 'files' / name, target)
            elif target.exists():
                target.unlink()
        if had_dropin:
            shutil.copy2(backup / 'dropin', DROPIN)
        elif DROPIN.exists():
            DROPIN.unlink()
        try:
            command('systemctl', 'daemon-reload'); command('systemctl', 'restart', 'callbot-crm.service'); command('systemctl', 'restart', 'callbot-astra.service')
            end = time.monotonic() + 60
            while time.monotonic() < end:
                try:
                    if command('systemctl', 'is-active', 'callbot-crm.service') == 'active' and command('systemctl', 'is-active', 'callbot-astra.service') == 'active' and status('/') == 200:
                        break
                except (OSError, RuntimeError, http.client.HTTPException):
                    pass
                time.sleep(2)
            else:
                raise RuntimeError('Rollback health check failed')
        except Exception:
            print('Rollback restart failed; manual intervention required', file=sys.stderr)
        raise
    print('CRM deployment completed; backup:', backup)


if __name__ == '__main__':
    main()
