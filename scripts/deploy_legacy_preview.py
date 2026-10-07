"""Deploy prepared UI and send one owner-only preview. Read-only by default.

--apply authorizes a new saved viewing run and Telegram delivery of its
extended overview. --restart additionally restarts ONLY the main bot after
offline tests and access checks. No migrations, ASR or model requests.
Run from an ordinary SSH terminal with access to systemd and .env.
"""
import argparse
import asyncio
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone

PROJECT = Path('/opt/callbot-astra')
MAIN = 'callbot-astra.service'
OTHERS = ('callbot-astra-worker.service', 'callbot-astra-queue-runner.service',
          'callbot-astra-trainer.service')
KEY = 'preview:legacy:20261006-deploy-v1'
FILES = ('methodology/config.json', 'automatic/config.json', 'unified/render.py',
         'unified/store.py', 'unified/ui.py', 'unified/reanalysis.py',
         'unified/extended_review.py', 'unified/overview_cache.py')
LATEST = """SELECT c.id FROM calls c JOIN astra_analysis a ON a.call_id=c.id
 WHERE c.client_id=$1 AND c.status='analyzed' AND a.status='analyzed'
 AND a.level IN ('✅','⚠️','❌') AND a.score IS NOT NULL
 AND length(trim(coalesce(c.transcript,''))) >= 80
 ORDER BY c.call_started_at DESC NULLS LAST,c.id DESC LIMIT 1"""


def selection(min_duration=0):
    if type(min_duration) is not int or not 0 <= min_duration <= 86400:
        raise ValueError('Minimum duration must be an integer from 0 to 86400 seconds')
    if min_duration == 0:
        return LATEST, (), KEY
    return (LATEST.replace(' ORDER BY', ' AND c.duration_seconds >= $2\n ORDER BY'),
            (min_duration,), KEY+f':min{min_duration}')


def offline_checks():
    code = """
import os, unittest
from unittest.mock import patch, AsyncMock
from aiogram.client.session.aiohttp import AiohttpSession
fake={'CVC_API_KEY':'synthetic-test-key','BOT_TOKEN':'123456:synthetic-test-token',
      'TRAIN_BOT_TOKEN':'123456:synthetic-test-token'}
with patch.dict(os.environ,fake,clear=True), patch('dotenv.load_dotenv',return_value=False), \\
     patch.object(AiohttpSession,'make_request',AsyncMock(side_effect=AssertionError('Unexpected Telegram request'))):
 suite=unittest.defaultTestLoader.loadTestsFromNames([
   'tests.test_legacy_mode','tests.test_extended_review','tests.test_unified.RendererTests',
   'tests.test_report_reanalysis.CombinedStyleTests','tests.test_methodology',
   'tests.test_automatic.AutomaticMenuTests','tests.test_deploy_legacy_preview'])
 result=unittest.TextTestRunner(verbosity=1).run(suite)
 raise SystemExit(not result.wasSuccessful())
"""
    subprocess.run([sys.executable, '-c', code], cwd=PROJECT, check=True)


def active(unit):
    return subprocess.check_output(['systemctl', 'show', unit, '-p', 'ActiveState', '--value'],
                                   text=True).strip()


def restart_main():
    workdir = subprocess.check_output(['systemctl', 'show', MAIN, '-p', 'WorkingDirectory', '--value'],
                                     text=True).strip()
    entry = subprocess.check_output(['systemctl', 'show', MAIN, '-p', 'ExecStart', '--value'],
                                   text=True)
    if workdir != str(PROJECT) or str(PROJECT/'demo_bot.py') not in entry:
        raise ValueError('Main bot unit does not point to this project')
    before = {unit: active(unit) for unit in OTHERS}
    started = str(int(time.time()))
    subprocess.run(['systemctl', 'restart', MAIN], check=True)
    deadline = time.monotonic() + 20
    while active(MAIN) != 'active':
        if time.monotonic() > deadline:
            raise RuntimeError('Main bot did not become active')
        time.sleep(.5)
    # Catch immediate startup failures, without restarting another service.
    time.sleep(3)
    if active(MAIN) != 'active':
        raise RuntimeError('Main bot failed during startup')
    after = {unit: active(unit) for unit in OTHERS}
    if before != after:
        raise RuntimeError('Other service state changed; inspect before sending preview')
    recent = subprocess.check_output(['journalctl', '-u', MAIN, '--since', '@'+started,
                                     '-n', '100', '--no-pager', '-o', 'json'], text=True)
    errors = 0
    for line in recent.splitlines():
        message = str(json.loads(line).get('MESSAGE', ''))
        if any(marker in message for marker in ('Traceback (most recent call last)',
                                                 ' ERROR ', ' CRITICAL ', 'SyntaxError:',
                                                 'ModuleNotFoundError:', 'ImportError:')):
            errors += 1
    # Print only an aggregate: journal lines may contain private information.
    print('Startup log error entries:', errors)
    if errors:
        raise RuntimeError('Startup logs contain errors; inspect before sending preview')
    print('Main bot active; other service states unchanged.')


def backup_sources():
    point = PROJECT/'docs'/'.rollback'/('deploy-preview-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    point.mkdir(mode=0o700, parents=True)
    (point/'.gitignore').write_text('*\n')
    for name in FILES:
        dest = point/name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PROJECT/name, dest)
    print('Prepared source backup:', point)


async def recipient(pool):
    from tools import Forbidden, resolve_actor
    rows = await pool.fetch("""SELECT telegram_user_id FROM employees
      WHERE lower(telegram_username)='im6risha1vanov' AND role='owner'
      AND telegram_user_id IS NOT NULL""")
    if len(rows) != 1:
        raise Forbidden('Owner Telegram binding is missing or ambiguous')
    actor = await resolve_actor(pool, rows[0]['telegram_user_id'])
    if actor is None or actor.role != 'owner':
        raise Forbidden('Owner permission check failed')
    return actor


async def prepare_preview(pool, actor, *, min_duration=0):
    from tools import Forbidden
    from unified import store
    if actor is None or actor.role != 'owner':
        raise Forbidden('Owner-only preview')
    query, params, request_key = selection(min_duration)
    async with pool.acquire() as conn, conn.transaction():
        # Repeated executions use the same saved call even if newer calls arrive.
        await conn.execute('SELECT pg_advisory_xact_lock(hashtextextended($1,0))',
                           f'{actor.client_id}:{request_key}')
        run_id = await conn.fetchval('SELECT id FROM call_report_runs WHERE client_id=$1 AND source_key=$2 ORDER BY id LIMIT 1',
                                     actor.client_id, request_key)
        if run_id is None:
            call_id = await conn.fetchval(query, actor.client_id, *params)
            if call_id is None:
                raise ValueError('No suitable saved call for this owner')
            run_id = await store.capture(conn, call_id, actor.client_id, request_key, fresh=False)
    row = await store.access(pool, actor, run_id)
    if row['configuration'].get('expect_course') or row['configuration'].get('expect_combined'):
        raise ValueError('Preview must contain only the original saved analysis')
    if min_duration and int(row.get('metadata', {}).get('duration_seconds') or 0) < min_duration:
        raise ValueError('Saved preview does not meet the requested minimum duration')
    row = await store.prepare(pool, run_id, wait=False)
    return row


async def run(args):
    if socket.gethostname().split('.')[0] != '9015421-nt422325' or Path.cwd().resolve() != PROJECT:
        raise ValueError('Wrong server or project')
    course = json.loads((PROJECT/'methodology/config.json').read_text())
    automatic = json.loads((PROJECT/'automatic/config.json').read_text())
    if course.get('mode') != 'legacy' or automatic.get('enabled') is not False:
        raise ValueError('Course analysis must be disabled before deployment')
    min_duration = getattr(args, 'min_duration', 0)
    query, params, _ = selection(min_duration)
    if args.apply:
        offline_checks()
    from dotenv import load_dotenv
    load_dotenv(PROJECT/'.env', override=True)
    import os
    import asyncpg
    from aiogram import Bot
    from unified import store, ui, overview_cache
    from unified.extended_review import VERSION
    pool = await asyncpg.create_pool(os.environ['DATABASE_URL'], min_size=1, max_size=2)
    bot = None
    try:
        actor = await recipient(pool)
        if not await store.installed(pool):
            raise ValueError('Unified report tables unavailable; no migration will be run')
        call_id = await pool.fetchval(query, actor.client_id, *params)
        if call_id is None:
            raise ValueError('No suitable saved call for this owner')
        print('Latest suitable saved call:', call_id)
        if not args.apply:
            print('Read-only check. No service restart, database writes or Telegram sends.')
            return
        bot = Bot(token=os.environ['BOT_TOKEN'])
        # Check token/network before restarting. getMe does not message users.
        await bot.get_me()
        backup_sources()
        if args.restart:
            restart_main()
        elif active(MAIN) != 'active':
            raise RuntimeError('Main bot is not active')
        row = await prepare_preview(pool, actor, min_duration=min_duration)
        if getattr(args, 'reopen', False):
            receipt = await overview_cache.delivered_view(pool, actor, row['id'])
            if receipt:
                await overview_cache.reopen(pool, actor, row['id'], receipt.get('generation', 0))
        await ui.show(bot, pool, actor, row['id'], detailed=True)
        saved = await store.access(pool, actor, row['id'])
        cache = overview_cache.decode(saved['expanded'])
        receipt = cache['entries'][VERSION].get('delivery', {}).get(str(actor.telegram_user_id), {})
        parts = receipt.get('parts', [])
        if not parts or any(part['status'] != 'sent' for part in parts):
            raise RuntimeError('Delivery is incomplete or uncertain; no blind retry')
        print('Preview delivered to the verified owner. Call:', row['call_id'],
              'run:', row['id'], 'Telegram message IDs:', [part['message_id'] for part in parts])
    finally:
        if bot is not None:
            await bot.session.close()
        await pool.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--restart', action='store_true')
    parser.add_argument('--min-duration', type=int, default=0,
                        help='Minimum call duration in seconds, e.g. 360 for six minutes')
    parser.add_argument('--reopen', action='store_true',
                        help='Explicitly show the saved preview again, without a new model request')
    args = parser.parse_args()
    if args.restart and not args.apply:
        parser.error('--restart requires --apply')
    if args.reopen and not args.apply:
        parser.error('--reopen requires --apply')
    if not 0 <= args.min_duration <= 86400:
        parser.error('--min-duration must be between 0 and 86400 seconds')
    try:
        asyncio.run(run(args))
    except Exception as exc:
        # Provider/DB exceptions may contain credentials; never dump their text.
        print('Deployment/send did not complete:', type(exc).__name__, file=sys.stderr)
        raise SystemExit(1)
