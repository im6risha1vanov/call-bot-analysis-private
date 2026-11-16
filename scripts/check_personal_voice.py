#!/usr/bin/env python3
"""Read-only production verification; never sends a Telegram message."""
import asyncio
import os
import subprocess
import shutil
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
    invocation = subprocess.run(['systemctl','show','callbot-astra.service','-p','InvocationID','--value'],check=True,capture_output=True,text=True).stdout.strip()
    if len(invocation) != 32 or any(c not in '0123456789abcdef' for c in invocation):
        raise RuntimeError('Main bot invocation is unavailable')
    marker = subprocess.run(['journalctl','_SYSTEMD_INVOCATION_ID='+invocation,'--grep=Personal reminder loop started successfully','-o','cat','--no-pager'],capture_output=True,text=True)
    print('Reminder loop first successful tick:', 'yes' if marker.returncode == 0 and marker.stdout.strip() else 'not observed')
    errors = subprocess.run(['journalctl','_SYSTEMD_INVOCATION_ID='+invocation,'--grep=Personal reminder loop failed','-o','cat','--no-pager'],capture_output=True,text=True)
    print('Reminder loop errors in current process:', 'yes' if errors.returncode == 0 and errors.stdout.strip() else 'none observed')
    os.environ['CRM_PERSONAL_PILOT_TELEGRAM_IDS'] = raw
    settings = dotenv_values(PROJECT / '.env')
    if not settings.get('DEEPGRAM_API_KEY') or not shutil.which('ffmpeg'):
        raise RuntimeError('Deepgram credential or ffmpeg missing')
    print('Deepgram credential and ffmpeg available.')
    dsn = settings.get('DATABASE_URL')
    if not dsn:
        raise RuntimeError('Database configuration is missing')
    sys.path.insert(0, str(PROJECT))
    from personal_telegram import authorized
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=1)
    try:
        if await pool.fetchval('SELECT current_database()') != 'callbot':
            raise RuntimeError('Unexpected database')
        for table in ('crm_personal_telegram_events', 'crm_personal_telegram_state', 'crm_personal_voice_drafts', 'crm_personal_reminder_delivery'):
            exists = await pool.fetchval('SELECT to_regclass($1) IS NOT NULL', 'public.' + table)
            if not exists:
                raise RuntimeError('Required Telegram table missing: ' + table)
            for privilege in ('SELECT', 'INSERT', 'UPDATE', 'DELETE'):
                if not await pool.fetchval('SELECT has_table_privilege(current_user, $1, $2)', 'public.' + table, privilege):
                    raise RuntimeError('Required Telegram table permission missing: ' + table + ' ' + privilege)
        for trigger in ('crm_personal_reminder_revision_trigger', 'crm_personal_profile_timezone_revision_trigger'):
            if not await pool.fetchval("SELECT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname=$1 AND NOT tgisinternal AND tgenabled <> 'D')",trigger):
                raise RuntimeError('Required reminder trigger missing: ' + trigger)
        counts = await pool.fetchrow("""SELECT count(*) AS active_tasks,
            count(*) FILTER (WHERE t.reminders_enabled AND t.status NOT IN ('done','cancelled') AND
              coalesce(t.reminder_at,least(t.due_at,(date_trunc('day',t.due_at AT TIME ZONE p.timezone)+interval '9 hours') AT TIME ZONE p.timezone))<=now()) AS due_now
            FROM crm_personal_tasks t JOIN crm_personal_profiles p ON p.id=t.owner_id
            WHERE p.telegram_user_id=ANY($1::bigint[])""", list(EXPECTED))
        print('Pilot tasks:', counts['active_tasks'], '; reminders already due:', counts['due_now'])
        for tg in sorted(EXPECTED):
            user = SimpleNamespace(id=tg)
            chat = SimpleNamespace(id=tg, type='private')
            if not await authorized(pool, user, chat):
                raise RuntimeError('Pilot is not authorized: ' + str(tg))
            profile = await pool.fetchval('SELECT 1 FROM crm_personal_profiles WHERE telegram_user_id=$1', tg)
            print('Pilot', tg, 'authorized; profile', 'exists' if profile else 'not yet created')
        print('Main bot allowlist and voice/reminder tables/permissions verified.')
    finally:
        await pool.close()


if __name__ == '__main__':
    try:
        asyncio.run(check())
    except Exception as exc:
        # Avoid printing connection strings or exception details from libraries.
        print('Verification failed:', str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__)
        raise SystemExit(1)
