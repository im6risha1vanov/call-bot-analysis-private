#!/usr/bin/env python3
"""Read-only production verification; never sends a Telegram message."""
import asyncio
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

EXPECTED = {841868875, 304327068}
PROJECT = Path('/opt/callbot-astra')


def main_bot_allowlist():
    result = subprocess.run(['systemctl', 'show', 'callbot-astra.service', '-p', 'MainPID', '--value'],
                            check=True, capture_output=True, text=True)
    pid = int(result.stdout.strip())
    if pid <= 0:
        raise RuntimeError('Main bot has no running process')
    values = {}
    for item in Path(f'/proc/{pid}/environ').read_bytes().split(b'\0'):
        key, sep, value = item.partition(b'=')
        if sep and key == b'CRM_PERSONAL_PILOT_TELEGRAM_IDS':
            values[key] = value
    raw = values.get(b'CRM_PERSONAL_PILOT_TELEGRAM_IDS', b'').decode('ascii')
    parts = raw.split(',')
    if any(not part.strip().isdigit() for part in parts):
        raise RuntimeError('Main bot allowlist is missing or invalid')
    if {int(part.strip()) for part in parts} != EXPECTED:
        raise RuntimeError('Main bot allowlist does not match the two pilots')
    return raw


async def check():
    import asyncpg
    from dotenv import dotenv_values
    raw = main_bot_allowlist()
    os.environ['CRM_PERSONAL_PILOT_TELEGRAM_IDS'] = raw
    dsn = dotenv_values(PROJECT / '.env').get('DATABASE_URL')
    if not dsn:
        raise RuntimeError('Database configuration is missing')
    sys.path.insert(0, str(PROJECT))
    from personal_telegram import authorized
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=1)
    try:
        if await pool.fetchval('SELECT current_database()') != 'callbot':
            raise RuntimeError('Unexpected database')
        for table in ('crm_personal_telegram_events', 'crm_personal_telegram_state'):
            exists = await pool.fetchval('SELECT to_regclass($1) IS NOT NULL', 'public.' + table)
            if not exists:
                raise RuntimeError('Required Telegram table missing: ' + table)
            for privilege in ('SELECT', 'INSERT', 'UPDATE', 'DELETE'):
                if not await pool.fetchval('SELECT has_table_privilege(current_user, $1, $2)', 'public.' + table, privilege):
                    raise RuntimeError('Required Telegram table permission missing: ' + table + ' ' + privilege)
        for tg in sorted(EXPECTED):
            user = SimpleNamespace(id=tg)
            chat = SimpleNamespace(id=tg, type='private')
            if not await authorized(pool, user, chat):
                raise RuntimeError('Pilot is not authorized: ' + str(tg))
            profile = await pool.fetchval('SELECT 1 FROM crm_personal_profiles WHERE telegram_user_id=$1', tg)
            print('Pilot', tg, 'authorized; profile', 'exists' if profile else 'not yet created')
        print('Main bot allowlist and Telegram tables/permissions verified.')
    finally:
        await pool.close()


if __name__ == '__main__':
    try:
        asyncio.run(check())
    except Exception as exc:
        # Avoid printing connection strings or exception details from libraries.
        print('Verification failed:', str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__)
        raise SystemExit(1)
