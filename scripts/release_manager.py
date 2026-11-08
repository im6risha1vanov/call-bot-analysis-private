#!/usr/bin/env python3
"""Installed outside the checkout so it survives a complete code rollback."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
from http.client import HTTPConnection, HTTPException
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time

PROJECT = Path('/opt/callbot-astra')
STATE = Path('/opt/callbot-astra-releases')
BASELINE = '52e4d575c03788d09194346362b1e06ef4476feb'
UNITS = ['callbot-astra.service', 'callbot-astra-worker.service',
         'callbot-astra-queue-runner.service', 'callbot-astra-trainer.service']
CRM_UNIT = 'callbot-crm.service'
CRM_HTTP_PORT = 8095
CRM_READY_TIMEOUT_SECONDS = 120


def command(args, *, cwd=PROJECT, umask=-1):
    result = subprocess.run(args, cwd=cwd, umask=umask, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        # Do not expose remote URLs, database credentials or arbitrary journal text.
        raise RuntimeError(f'Command failed: {args[0]} (exit {result.returncode})')
    return result.stdout.strip()


def guard():
    if socket.gethostname().split('.')[0] != '9015421-nt422325' or PROJECT.resolve() != Path('/opt/callbot-astra'):
        raise RuntimeError('Wrong server or checkout')
    if os.geteuid() != 0:
        raise RuntimeError('Run with sudo: switching services requires root')


def commit(ref):
    if not re.fullmatch('[0-9a-f]{40}', ref):
        raise RuntimeError('Only pinned full commit IDs are accepted')
    resolved = command(['git', 'rev-parse', '--verify', ref+'^{commit}'])
    if resolved != ref:
        raise RuntimeError('Commit mismatch')
    return resolved


def manifest(ref):
    files = subprocess.check_output(['git','ls-tree','-r','--name-only','-z',ref],cwd=PROJECT).split(b'\0')
    return {name.decode():hashlib.sha256(subprocess.check_output(['git','show',ref+':'+name.decode()],cwd=PROJECT)).hexdigest()
            for name in files if name}


def verify_files(ref, *, baseline_manifest=None):
    expected = manifest(ref)
    if baseline_manifest is None and ref == BASELINE and (STATE/'original-manifest.json').exists():
        baseline_manifest = json.loads((STATE/'original-manifest.json').read_text())
    if baseline_manifest is not None and expected != baseline_manifest:
        raise RuntimeError('Original Git files no longer match saved baseline manifest')
    if command(['git','rev-parse','HEAD']) != ref:
        raise RuntimeError('HEAD mismatch')
    for name,digest in expected.items():
        path=PROJECT/name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
            raise RuntimeError('Tracked file checksum mismatch')
    if command(['git','status','--porcelain']):
        raise RuntimeError('Checkout is not clean')
    return len(expected)


async def database(action, *, release=None):
    import asyncpg
    from dotenv import dotenv_values
    dsn = dotenv_values(PROJECT/'.env').get('DATABASE_URL')
    if not dsn:
        raise RuntimeError('Database configuration is missing')
    conn = await asyncpg.connect(dsn)
    try:
        if action=='migrate':
            names=command(['git','ls-tree','-r','--name-only',release,'--','migrations']).splitlines()
            migrations=sorted(n for n in names if re.fullmatch(r'migrations/\d{3}_[a-z_]+\.sql',n))
            if not migrations:
                raise RuntimeError('No release migrations found')
            async with conn.transaction():
                for name in migrations:
                    migration=subprocess.check_output(['git','show',release+':'+name],cwd=PROJECT).decode()
                    if migration.strip().startswith('BEGIN;') and migration.strip().endswith('COMMIT;'):
                        migration=migration.strip().removeprefix('BEGIN;').removesuffix('COMMIT;')
                    await conn.execute(migration)
            return
        processing=await conn.fetchval("SELECT count(*) FROM tasks WHERE status='processing'")
        course_active=0
        if await conn.fetchval("SELECT to_regclass('methodology_training_context') IS NOT NULL"):
            course_active=await conn.fetchval("SELECT count(*) FROM training_sessions t JOIN methodology_training_context mc ON mc.session_id=t.id WHERE t.status='active'")
        unconfirmed_events = 0
        if await conn.fetchval("""SELECT EXISTS (SELECT 1 FROM pg_attribute
                WHERE attrelid=to_regclass('automation_events') AND attname='claimed_at' AND NOT attisdropped)"""):
            unconfirmed_events = await conn.fetchval(
                'SELECT count(*) FROM automation_events WHERE claimed_at IS NOT NULL AND processed_at IS NULL')
        unresolved_deliveries = 0
        if await conn.fetchval("SELECT to_regclass('review_delivery') IS NOT NULL"):
            unresolved_deliveries = await conn.fetchval(
                "SELECT count(*) FROM review_delivery WHERE status IN ('pending', 'sending', 'uncertain')")
        return {'processing_tasks': processing, 'active_course_training': course_active,
                'unconfirmed_automation_events': unconfirmed_events,
                'unresolved_review_deliveries': unresolved_deliveries}
    finally:
        await conn.close()


def units_for(ref):
    has_crm = command(['git', 'ls-tree', '--name-only', ref, '--', 'crm/web.py']) == 'crm/web.py'
    return [*UNITS, CRM_UNIT] if has_crm else UNITS


def crm_http_ready():
    connection = HTTPConnection('127.0.0.1', CRM_HTTP_PORT, timeout=2)
    try:
        connection.request('GET', '/api/me')
        with connection.getresponse() as response:
            content_type = response.getheader('Content-Type', '').split(';', 1)[0].strip().lower()
            return response.status == 401 and content_type == 'application/json'
    except (OSError, HTTPException):
        return False
    finally:
        connection.close()


def healthy(units):
    active = all(command(['systemctl','is-active',unit])=='active' for unit in units)
    return active and (CRM_UNIT not in units or crm_http_ready())


def wait_healthy(units):
    timeout = CRM_READY_TIMEOUT_SECONDS if CRM_UNIT in units else 10
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if healthy(units):
                time.sleep(2)
                if healthy(units):
                    return
        except RuntimeError:
            pass
        time.sleep(1)
    raise RuntimeError('Astra services or the CRM HTTP endpoint did not become ready')


def status():
    current = command(['git', 'rev-parse', 'HEAD'])
    services = [{'unit': unit, 'active': command(['systemctl', 'show', '--property=ActiveState', '--value', unit])}
                for unit in units_for(current)]
    return {'commit': current, 'services': services, **asyncio.run(database('status'))}


def verify_rollback_deliveries(state):
    if state['unconfirmed_automation_events']:
        raise RuntimeError('Reconcile unconfirmed automation events before rollback to avoid sending them twice')
    if state['unresolved_review_deliveries']:
        raise RuntimeError('Reconcile outstanding review deliveries before rollback to avoid losing notifications')


def switch(target, *, rollback=False, schema_compatible=False, baseline_manifest=None):
    commit(target)
    if command(['git','status','--porcelain']):
        raise RuntimeError('Uncommitted changes: save them before switching releases')
    current=command(['git','rev-parse','HEAD'])
    if rollback and not schema_compatible and command(['git', 'diff', '--name-only', current, target, '--', 'migrations']):
        raise RuntimeError('Migration files differ. Verify the database is compatible with the rollback target before using --schema-compatible')
    current_units = units_for(current)
    target_units = units_for(target)
    switching_units = list(dict.fromkeys(current_units + target_units))
    for unit in switching_units:
        if command(['systemctl', 'show', '--property=LoadState', '--value', unit]) != 'loaded':
            raise RuntimeError(f'Required service is not loaded: {unit}')
    busy=asyncio.run(database('status'))
    if busy['processing_tasks'] or busy['active_course_training']:
        raise RuntimeError('Processing is in progress. Wait for tasks and finish course training with /stop before switching')
    if rollback:
        verify_rollback_deliveries(busy)
    checkout_attempted = migration_attempted = start_attempted = False
    try:
        command(['systemctl','stop',*switching_units])
        # A task might have been claimed between the initial read and service shutdown.
        busy=asyncio.run(database('status'))
        if busy['processing_tasks'] or busy['active_course_training']:
            raise RuntimeError('Work started during shutdown; restoring running services without changing code')
        if rollback:
            verify_rollback_deliveries(busy)
        checkout_attempted = True
        # The controller runs as root. Systemd services can use a different user.
        command(['git','checkout','--detach',target], umask=0o022)
        files=verify_files(target, baseline_manifest=baseline_manifest)
        if not rollback:
            migration_attempted = True
            asyncio.run(database('migrate', release=target))
        start_attempted = True
        command(['systemctl','start',*target_units])
        wait_healthy(target_units)
    except BaseException as error:
        if migration_attempted:
            try:
                command(['systemctl', 'stop', *switching_units])
            except RuntimeError:
                raise RuntimeError('Failure after migration attempt. Automatic rollback disabled; check service and database state') from error
            raise RuntimeError('Failure after migration attempt. Services stopped; verify database state before recovery') from error
        if start_attempted:
            command(['systemctl','stop',*switching_units])
        if checkout_attempted:
            command(['git','checkout','--detach',current], umask=0o022)
        command(['systemctl','start',*current_units])
        wait_healthy(current_units)
        raise
    print(json.dumps({'commit':target,'verified_tracked_files':files,'services':f'{len(target_units)} active',
                      'database':'preserved; no down migration'},ensure_ascii=False))


def run_plan(args, config_path):
    if args.action=='init':
        if config_path.exists():
            raise RuntimeError('Release configuration already exists')
        release=commit(args.release or '')
        baseline=commit(args.baseline if args.plan else BASELINE)
        config={'baseline': baseline, 'release': release}
        if args.plan:
            if baseline == release:
                raise RuntimeError('Baseline and release must be distinct')
            if command(['git', 'rev-parse', 'HEAD']) != baseline or command(['git', 'status', '--porcelain']):
                raise RuntimeError('A clean checkout of the pinned baseline is required')
            if any(CRM_UNIT not in units_for(ref) for ref in (baseline, release)):
                raise RuntimeError('Both commits in a CRM release plan must contain CRM')
            config['baseline_manifest'] = manifest(baseline)
        else:
            (STATE/'original-manifest.json').write_text(json.dumps(manifest(baseline),indent=2)+'\n')
        pending = config_path.with_suffix('.tmp')
        pending.write_text(json.dumps(config,indent=2)+'\n')
        pending.replace(config_path)
        print('Pinned baseline and release; original file checksums saved')
        return
    config=json.loads(config_path.read_text())
    if not args.plan and config['baseline']!=BASELINE:
        raise RuntimeError('Unexpected rollback baseline')
    if args.action=='status':
        print(json.dumps(status()))
        return
    current = command(['git', 'rev-parse', 'HEAD'])
    if current not in (config['baseline'], config['release']):
        raise RuntimeError('Current checkout is outside the pinned release plan')
    rollback = args.action == 'rollback'
    if args.plan and current != config['release' if rollback else 'baseline']:
        raise RuntimeError('Current checkout is not the starting commit for this operation')
    switch(config['baseline' if rollback else 'release'], rollback=rollback,
           schema_compatible=args.schema_compatible,
           baseline_manifest=config['baseline_manifest'] if args.plan and rollback else None)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['init','status','deploy','rollback'])
    parser.add_argument('--release')
    parser.add_argument('--plan', help='name of a separate CRM release plan')
    parser.add_argument('--baseline', help='current full commit ID when initializing a CRM release plan')
    parser.add_argument('--schema-compatible', action='store_true', help='confirm rollback database compatibility has been verified')
    args=parser.parse_args()
    if args.schema_compatible and args.action != 'rollback':
        parser.error('--schema-compatible is only valid for rollback')
    if args.plan is not None and not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,47}', args.plan):
        parser.error('--plan must contain 1 to 48 lowercase letters, digits or hyphens, starting with a letter or digit')
    if (args.baseline is not None) != (args.plan is not None and args.action == 'init'):
        parser.error('--baseline is required when initializing a CRM release plan and is valid only then')
    guard()
    config_path=STATE / (f'release-{args.plan}.json' if args.plan else 'release.json')
    if args.action == 'status':
        run_plan(args, config_path)
        return

    import fcntl

    if args.action == 'init':
        STATE.mkdir(exist_ok=True, mode=0o755)
    with (STATE/'switch.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError('Another release operation is in progress') from error
        run_plan(args, config_path)


if __name__=='__main__':
    try:
        main()
    except Exception as exc:
        print(f'Release operation stopped: {str(exc) if isinstance(exc,RuntimeError) else type(exc).__name__}',file=sys.stderr)
        sys.exit(1)
