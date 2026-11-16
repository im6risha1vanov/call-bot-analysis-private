#!/usr/bin/env python3
"""Read-only post-deployment verification. Never prints credential values."""
import argparse
import asyncio
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

PROJECT = Path('/opt/callbot-astra')
PILOT = {841868875, 304327068}
TABLES = ('crm_personal_profiles', 'crm_personal_projects', 'crm_personal_tasks', 'crm_personal_task_history')
INVITES = {1: 841868875, 3: 841868875, 2: 304327068, 6: 304327068}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def service_allowlist():
    result = subprocess.run(['systemctl', 'show', 'callbot-crm.service', '-p', 'MainPID', '--value'], capture_output=True, text=True, check=True)
    pid = int(result.stdout.strip())
    if pid <= 0:
        raise RuntimeError('CRM process is not running')
    entries = Path(f'/proc/{pid}/environ').read_bytes().split(b'\0')
    values = [e.split(b'=', 1)[1].decode('ascii') for e in entries if e.startswith(b'CRM_PERSONAL_PILOT_TELEGRAM_IDS=')]
    if len(values) != 1:
        raise RuntimeError('CRM process has no unique personal pilot setting')
    parts = values[0].split(',')
    if any(not x.strip().isdigit() for x in parts) or {int(x.strip()) for x in parts} != PILOT:
        raise RuntimeError('CRM pilot setting differs from expected identities')
    return values[0]


def check_package(manifest, pin):
    if len(pin) != 64 or digest(manifest) != pin.lower():
        raise RuntimeError('Manifest hash mismatch')
    entries = json.loads(manifest.read_text(encoding='utf-8'))
    if not entries or not isinstance(entries, dict):
        raise RuntimeError('Empty or invalid manifest')
    for name, expected in entries.items():
        rel = Path(name)
        if rel.is_absolute() or '..' in rel.parts:
            raise RuntimeError('Invalid manifest path')
        path = PROJECT / rel
        if path.is_symlink() or not path.is_file() or digest(path) != expected:
            raise RuntimeError('Deployed file differs from package: ' + name)
    print(f'Package files verified: {len(entries)}')


async def verify():
    import asyncpg
    from dotenv import dotenv_values
    sys.path.insert(0, str(PROJECT))
    from crm import auth, personal
    from tools import resolve_actor

    config = dotenv_values(PROJECT / '.env')
    dsn = config.get('DATABASE_URL')
    if not dsn:
        raise RuntimeError('Database configuration missing')
    conn = await asyncpg.connect(dsn)
    try:
        async with conn.transaction(readonly=True):
            if await conn.fetchval('SELECT current_database()') != 'callbot':
                raise RuntimeError('Unexpected database')
            for table in TABLES:
                if not await conn.fetchval('SELECT to_regclass($1) IS NOT NULL AND has_table_privilege(current_user,$1,\'SELECT\')', 'public.' + table):
                    raise RuntimeError('Missing table or SELECT permission: ' + table)
                count = await conn.fetchval('SELECT count(*) FROM public.' + table)
                print(f'{table}: {count} rows')
            for invite_id, tg in INVITES.items():
                row = await conn.fetchrow('SELECT id,login,client_id,telegram_user_id,revoked_at FROM crm_invites WHERE id=$1', invite_id)
                if not row or row['revoked_at'] is not None or row['telegram_user_id'] != tg:
                    raise RuntimeError(f'Invite {invite_id} is inactive or differs from verified identity')
                actor = await resolve_actor(conn, tg)
                if not actor or actor.client_id != row['client_id']:
                    raise RuntimeError(f'Invite {invite_id} has no matching actor')
                viewer = auth.Viewer(role='director' if actor.role in ('owner','head') else 'manager', actor=actor, invite_id=invite_id)
                if await personal.identity(conn, viewer) != tg:
                    raise RuntimeError(f'Invite {invite_id} failed personal identity check')
                if await personal.identity(conn, replace(viewer, support=True, support_by=invite_id)) is not None or await personal.identity(conn, replace(viewer, test=True)) is not None:
                    raise RuntimeError('Test or support access was not denied')
                print(f'Invite {invite_id}: login={row["login"] or "(legacy)"}, identity verified')
            print('Database access and pilot identity checks passed')
    finally:
        await conn.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--manifest-sha256', required=True)
    args = parser.parse_args()
    check_package(args.manifest, args.manifest_sha256)
    os.environ['CRM_PERSONAL_PILOT_TELEGRAM_IDS'] = service_allowlist()
    print('CRM process pilot allowlist verified')
    asyncio.run(verify())


if __name__ == '__main__':
    main()
